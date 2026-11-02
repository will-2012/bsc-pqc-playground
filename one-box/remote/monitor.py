"""Private shared-directory telemetry; SSM is used only to launch collectors."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import math
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from model import base, unit, require


def read(path):
    with Path(path).open() as stream:
        text = stream.read(4 * 1024 * 1024 + 1)
    require(len(text) <= 4 * 1024 * 1024, 'Monitor record too large')
    value = json.loads(text)
    require(isinstance(value, dict), 'Monitor record must be an object')
    return value


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream)
    temp.replace(path)


def job_identity(runtime):
    try:
        job = read(runtime / 'load-job.json')
        return {k: job[k] for k in ('dataset', 'run')}
    except (OSError, ValueError, KeyError):
        return None


def service_state(c):
    result = subprocess.run(['systemctl', 'show', unit(c, 'bench'), '-p', 'ActiveState', '--value'],
                            capture_output=True, text=True, timeout=3)
    return result.stdout.strip() if result.returncode == 0 else 'unknown'


def request_stop(c, h, token, target):
    """Short environment lock protects job check plus nonblocking stop dispatch."""
    from agent import check_owner
    if 'bench' not in h['roles'] or target is None:
        return 'NOT_APPLICABLE'
    folder = Path(c['deploy_root']) / '.bsc-pqc-locks'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / (c['name'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_owner(c, token)
        current = job_identity(base(c) / 'runtime')
        if current != target:
            return 'JOB_CHANGED' if current is not None else 'NO_MATCHING_JOB'
        state = service_state(c)
        if state in ('inactive', 'failed'):
            return 'STOPPED'
        if state == 'deactivating':
            return 'DRAINING'
        expected = str(base(c) / 'current/one-box/remote/agent.py')
        command = subprocess.run(['systemctl', 'show', unit(c, 'bench'), '-p', 'ExecStart', '--value'],
                                 capture_output=True, text=True, timeout=3)
        require(command.returncode == 0 and expected in command.stdout, 'Foreign Bench service')
        subprocess.run(['systemctl', '--no-block', 'stop', unit(c, 'bench')],
                       check=True, capture_output=True, timeout=3)
        return 'STOP_REQUESTED'


class Lease:
    """Local monotonic lease; shared filesystem I/O never runs in watchdog thread."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.last_seen = time.monotonic()
        self.heartbeat = None
        self.stop = False
        self.exit = False
        self.state = None

    def update(self, control):
        if control.get('heartbeat') != self.heartbeat:
            self.heartbeat = control.get('heartbeat')
            self.last_seen = time.monotonic()
        self.stop |= bool(control.get('stopBench'))
        self.exit |= bool(control.get('stopCollector'))

    def expired(self):
        return time.monotonic() - self.last_seen > self.seconds


def collect_loop(request):
    from monitor_collect import Collector
    from agent import check_owner
    p = read(request)
    c, h = p['config'], p['host']
    check_owner(c, p['token'])
    session = p['monitor_session']
    shared = Path(h['shared']) / ('bsc-pqc-' + c['name']) / ('monitor-' + session)
    collector = Collector(c, h, base(c) / 'runtime')
    lease = Lease(p['monitor_lease'])
    target = p.get('monitor_target')
    auto = p['monitor_auto_stop'] and 'bench' in h['roles']
    closing, watchdog_done = None, threading.Event()
    def on_signal(*_):
        lease.exit = True
        if auto:
            lease.stop = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    def watchdog():
        while not watchdog_done.is_set():
            if auto and (lease.stop or lease.expired()):
                try:
                    lease.state = request_stop(c, h, p['token'], target)
                except Exception:
                    lease.state = 'STOP_UNCONFIRMED'
            watchdog_done.wait(2)
    guard = threading.Thread(target=watchdog, daemon=True)
    guard.start()
    seq = 0
    try:
        while True:
            started = time.monotonic()
            try:
                control = read(shared / 'control.json')
                require(control['session'] == session, 'Monitor session mismatch')
                lease.update(control)
            except (OSError, ValueError, KeyError):
                pass
            if lease.expired():
                lease.exit = True
                lease.stop |= auto
            try:
                sample = collector.collect()
            except Exception as e:
                sample = {'host': h['id'], 'sampledAt': int(time.time()*1000), 'error': type(e).__name__}
            seq += 1
            sample.update(session=session, sequence=seq, stopState=lease.state,
                          controllerLeaseExpired=lease.expired(), guardedJob=target if auto else None)
            write(shared / (h['id'] + '.json'), sample)
            if lease.exit:
                closing = closing or started
                if not auto or lease.state in ('STOPPED', 'NO_MATCHING_JOB', 'JOB_CHANGED'):
                    break
                if started - closing > 30:
                    sample['stopState'] = lease.state if lease.state == 'DRAINING' else 'STOP_UNCONFIRMED'
                    write(shared / (h['id'] + '.json'), sample)
                    break
            time.sleep(max(0, 2 - (time.monotonic() - started)))
    finally:
        if auto:
            try:
                lease.state = request_stop(c, h, p['token'], target)
            except Exception:
                lease.state = 'STOP_UNCONFIRMED'
        watchdog_done.set()
        guard.join(timeout=10)


def start_collector(p, request_path):
    """Called once through the normal owned agent; no long-lived environment lock."""
    c, h = p['config'], p['host']
    service = unit(c, 'monitor')
    state = subprocess.run(['systemctl', 'show', service, '-p', 'ActiveState', '--value'],
                           capture_output=True, text=True, timeout=5).stdout.strip()
    require(state not in ('active', 'activating', 'deactivating'), 'Another monitor collector is active')
    script = Path(request_path).parent / 'monitor.py'
    subprocess.run(['systemd-run', '--quiet', '--collect', '--unit', service,
                    '--property=UMask=0077', '--property=Restart=no',
                    '--property=TimeoutStopSec=15', '--property=StandardOutput=null',
                    '--property=StandardError=null', h.get('python', 'python3'),
                    str(script), 'collect', str(request_path)],
                   capture_output=True, check=True, timeout=15)
    return {'collector': 'started'}


class SharedSamples:
    """Use local receive age, never compare unsynchronized wall clocks."""
    def __init__(self, folder, hosts, session, stale):
        self.folder, self.hosts, self.session, self.stale = Path(folder), hosts, session, stale
        self.seen = {}

    def read(self, now):
        rows = {}
        for h in self.hosts:
            key = h['id']
            try:
                sample = read(self.folder / (key + '.json'))
                require(sample.get('session') == self.session and sample.get('host') == key,
                        'Foreign monitor sample')
                seq = sample['sequence']
                require(type(seq) is int and seq > 0, 'Invalid sample sequence')
                previous = self.seen.get(key)
                if previous is not None and seq < previous[0]:
                    raise ValueError('Out of order sample')
                if previous is None or seq > previous[0]:
                    self.seen[key] = (seq, now)
                age = now - self.seen[key][1]
                if age > self.stale:
                    rows[key] = {'error': 'STALE_SAMPLE', 'ageSeconds': age}
                else:
                    rows[key] = {**sample, 'ageSeconds': age}
            except (OSError, ValueError, KeyError):
                rows[key] = {'error': 'MISSING_SAMPLE'}
        return rows


def workload_label(result, rows):
    """Separate workload activity from chain health; missing samples are not idle."""
    states = []
    unknown = not rows
    for row in rows.values():
        bench = row.get('bench') or {}
        service = (row.get('components') or {}).get('bench', {}).get('service', bench.get('service'))
        if row.get('error'):
            unknown = True
        elif service == 'active':
            states.append((bench.get('progress') or {}).get('status', 'unknown'))
        elif service not in ('inactive', 'failed'):
            unknown = True
    if any(state == 'running' for state in states):
        return '压测中', 36
    if 'draining' in states:
        return '收尾中', 36
    if 'starting' in states:
        return '准备中', 36
    if states or unknown:
        return '负载未知', 33
    window = result.get('window') or {}
    if window.get('complete') and window.get('blocks') and window.get('transactions') is not None:
        return ('空载', 32) if window['transactions'] == 0 else ('链上有流量', 36)
    return '未压测', 33


def print_report(result, rows, settings=None):
    from monitor_model import DEFAULTS
    config = {**DEFAULTS, **(settings or {})}
    color = sys.stdout.isatty() and 'NO_COLOR' not in os.environ

    def mark(value, ok=None):
        text = '?' if value is None else str(value)
        if not color or value is None or ok is None:
            return text
        return f"\033[{32 if ok else 31}m{text}\033[0m"

    def fmt(value, ok=None):
        return mark(None if value is None else f'{value:.1f}', ok)

    reasons = result.get('reasons', [])
    window, bench = result.get('window') or {}, result.get('bench') or {}
    healthy = result['status'] == 'HEALTHY'
    status_ok = True if healthy else False if result['status'] == 'UNSTABLE' else None
    label, shade = workload_label(result, rows)
    load = f"\033[{shade}m{label}\033[0m" if color else label
    print()
    print(time.strftime('%H:%M:%S'), 'load=' + load,
          'health=' + mark(result['status'], status_ok),
          'stop=' + mark(result.get('stop', False), not result.get('stop', False)), flush=True)
    if reasons:
        print('  reasons=' + ','.join(mark(r, None if r == 'BENCH_BACKLOG' else False) for r in reasons), flush=True)
    ratio = window.get('gasRatio')
    complete = window.get('complete') is True
    blocks, transactions = window.get('blocks'), window.get('transactions')
    per_block = transactions / blocks if blocks and transactions is not None else None
    def interval(key):
        value = window.get(key)
        return fmt(value, value > 0 and value <= config['expected_block_ms'] * config['slow_block_factor']
                   if value is not None and complete else None)
    print(f"  chain: blocks={mark(window.get('blocks'), True if complete else None)} "
          f"tx={mark(window.get('transactions'), True if complete else None)} "
          f"tx/block={fmt(per_block, True if complete else None)} "
          f"userOps={mark(window.get('userOps'), True if complete else None)} "
          f"gas={fmt(None if ratio is None else ratio*100, True if healthy else None)}% "
          f"interval p50/p95/max={interval('intervalP50Ms')}/{interval('intervalP95Ms')}/{interval('intervalMaxMs')} ms "
          f"outOfTurn={mark(window.get('outOfTurn'), window.get('outOfTurn') == 0)} "
          f"slash={mark(window.get('slashEvents'), window.get('slashEvents') == 0)}", flush=True)
    if label not in ('空载', '未压测', '链上有流量'):
        print(f"  bench: target={fmt(bench.get('targetQps'))} accepted={fmt(bench.get('acceptedQps'))} "
              f"receiptTPS={fmt(bench.get('confirmedQps'))}", flush=True)

    def table(headers, records):
        # Headers/cells are ASCII; ANSI colors must not contribute to column width.
        def width(text):
            return len(re.sub(r'\x1b\[[0-9;]*m', '', text))
        widths = [max(width(row[i]) for row in [headers, *records]) for i in range(len(headers))]
        def line(record):
            return '  ' + ' | '.join(cell + ' ' * (size - width(cell)) for cell, size in zip(record, widths))
        print(line(headers))
        print('  ' + '-+-'.join('-' * size for size in widths))
        for record in records:
            print(line(record))

    chain_rows, resource_rows, notes = [], [], []
    for host, row in rows.items():
        head, safe, final = row.get('head') or {}, row.get('safe') or {}, row.get('finalized') or {}
        resource = row.get('resources') or {}
        host_bad = bool(row.get('error')) or any(r.startswith(host + ':') for r in reasons)
        host_ok = False if host_bad else True if healthy else None
        pool = result.get('hosts', {}).get(host, {}).get('txpool') or {}
        pool_ok = False if pool.get('sustained') else True if pool.get('available') else None
        delta = pool.get('delta')
        chain_rows.append([host,
            '/'.join(mark(value.get('number'), host_ok) for value in (head, safe, final)),
            mark(row.get('peers'), row.get('peers', 0) > 0 if row.get('peers') is not None else None),
            '/'.join(mark(pool.get(key), pool_ok) for key in ('pending', 'queued')),
            mark(pool.get('total'), pool_ok), mark(None if delta is None else f'{delta:+d}', pool_ok),
            mark(pool.get('growth'), pool_ok), fmt(pool.get('growthSeconds'), pool_ok), fmt(row.get('ageSeconds'))])
        components = row.get('components') or {}
        def service(role):
            value = components.get(role) or {}
            state = value.get('service')
            if state == 'inactive' and role == 'bench':
                return 'idle'
            ok = True if state == 'active' else False if state is not None else None
            return mark(state, ok) + '/' + fmt(value.get('cpuPct'))
        def mib(key):
            value = resource.get(key)
            return fmt(None if value is None else value / 1024**2)
        resource_rows.append([host, fmt(resource.get('cpuBusyPct')), fmt(resource.get('iowaitPct')),
            service('validator'), service('bundler'), service('bench'), mib('memoryAvailableBytes'),
            mib('diskReadBytesPerSecond') + '/' + mib('diskWriteBytesPerSecond'),
            mib('networkRxBytesPerSecond') + '/' + mib('networkTxBytesPerSecond')])
        if row.get('error'):
            notes.append(host + ': error=' + mark(row['error'], False))
        if row.get('stopState'):
            notes.append(host + ': stop=' + mark(row['stopState'], False if row['stopState'] == 'STOP_UNCONFIRMED' else None))
    if rows:
        table(['Node', 'Head/Safe/Final', 'Peers', 'Pool P/Q', 'Total', 'Delta', 'Growth', 'Grow(s)', 'Age(s)'], chain_rows)
        print()
        table(['Node', 'CPU%', 'IOwait%', 'Validator/%', 'Bundler/%', 'Bench/%', 'Free MiB', 'Disk R/W', 'Net RX/TX'], resource_rows)
        print('  Pool P/Q=pending/queued; service CPU: 100%=1 core; disk/net: MiB/s')
    for note in notes:
        print('  ' + note)
    sys.stdout.flush()


def execute(cluster, args):
    from monitor_model import MonitorModel
    require(5 <= args.interval <= 60, 'Monitor interval must be 5..60 seconds')
    require(math.isfinite(args.duration) and args.duration >= 0, 'Duration must be finite and nonnegative')
    import re
    if args.auto_stop:
        require(all(isinstance(v, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', v)
                    for v in (args.monitor_dataset, args.monitor_run)),
                'Auto-stop requires --monitor-dataset and --monitor-run')
    settings = read(args.monitor_policy) if args.monitor_policy else {}
    require(isinstance(settings, dict), 'Policy must be an object')
    settings = {**settings, 'interval': args.interval}
    model = MonitorModel(settings)
    hosts = cluster.c['hosts']
    session = uuid.uuid4().hex
    folder = cluster.shared / ('bsc-pqc-' + cluster.c['name']) / ('monitor-' + session)
    folder.mkdir(parents=True, mode=0o700)
    output = cluster.home / 'monitor' / session
    output.mkdir(parents=True, mode=0o700)
    lease = max(45, args.interval*4)
    control = {'session': session, 'heartbeat': 0, 'stopBench': False, 'stopCollector': False}
    control_lock = threading.Lock()
    heartbeat_stop = threading.Event()
    last_progress = [time.monotonic()]
    def heartbeat():
        while not heartbeat_stop.is_set():
            with control_lock:
                if time.monotonic() - last_progress[0] <= lease / 2:
                    control['heartbeat'] += 1
                    write(folder / 'control.json', control)
            heartbeat_stop.wait(2)
    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    def launch(h):
        return cluster.call(h, 'monitor-start', monitor_session=session,
                            monitor_auto_stop=args.auto_stop, monitor_lease=lease,
                            monitor_target={'dataset': args.monitor_dataset, 'run': args.monitor_run} if args.auto_stop else None)
    samples = SharedSamples(folder, hosts, session, stale=max(15, args.interval*2))
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
            futures = {pool.submit(launch, h): h['id'] for h in hosts}
            for future in as_completed(futures):
                future.result()
                last_progress[0] = time.monotonic()
                print(futures[future] + ': collector started', flush=True)
        print('Monitor: auto-stop=' + str(args.auto_stop) + '; Ctrl+C ends monitoring' +
              (' and requests Bench stop.' if args.auto_stop else '.'), flush=True)
        while True:
            now = time.monotonic()
            rows = samples.read(now)
            validators = {h['id']: rows[h['id']] for h in hosts if 'validator' in h['roles']}
            benches = {h['id']: ({'error': rows[h['id']]['error']} if rows[h['id']].get('error') else
                                 {**rows[h['id']].get('bench', {}), 'sequence': rows[h['id']].get('sequence')})
                       for h in hosts if 'bench' in h['roles']}
            analyzed_benches = benches
            if args.auto_stop:
                analyzed_benches = {key: (b if b.get('error') or
                    (b.get('dataset'), b.get('run')) == (args.monitor_dataset, args.monitor_run)
                    else {'service': 'inactive', 'progress': {}}) for key, b in benches.items()}
            result = model.observe(validators, now, benches=analyzed_benches)
            with control_lock:
                if args.auto_stop and result.get('stop'):
                    control['stopBench'] = True
            last_progress[0] = now
            from monitor_reconcile import reconcile
            result['reconciliation'] = reconcile(validators, benches)
            record = {'sampledAt': int(time.time()*1000), 'result': result, 'nodes': rows}
            write(output / 'latest.json', record)
            with (output / 'windows.jsonl').open('a') as stream:
                stream.write(json.dumps(record) + '\n')
            print_report(result, rows, settings)
            if args.duration and now - started >= args.duration:
                break
            time.sleep(max(0, args.interval - (time.monotonic() - now)))
    except KeyboardInterrupt:
        print('Ending monitor; preserving samples.', flush=True)
    finally:
        with control_lock:
            control['stopBench'] = bool(args.auto_stop)
            control['stopCollector'] = True
            control['heartbeat'] += 1
            write(folder / 'control.json', control)
        heartbeat_stop.set()
        pulse.join(timeout=3)
        if args.auto_stop:
            deadline = time.monotonic() + 20
            states = {}
            while time.monotonic() < deadline:
                latest = samples.read(time.monotonic())
                states = {h['id']: latest[h['id']].get('stopState') for h in hosts if 'bench' in h['roles']}
                if states and all(v in ('STOPPED', 'DRAINING', 'JOB_CHANGED', 'NO_MATCHING_JOB') for v in states.values()):
                    break
                time.sleep(2)
            print('Bench stop acknowledgements: ' + json.dumps(states), flush=True)
            print('DRAINING is not STOPPED; missing acknowledgement requires status check. Validators/Bundlers unchanged.', flush=True)
        print('Private monitor results: monitor/' + session, flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == 'collect':
        try:
            collect_loop(Path(sys.argv[2]))
        except Exception as error:
            from agent import record_error
            record_error(Path(sys.argv[2]).parent / 'error.log', error)
            raise SystemExit(1)
