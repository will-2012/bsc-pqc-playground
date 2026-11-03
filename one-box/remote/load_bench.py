"""Controller-side partitioning. Contains no machine identity or key material."""
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from model import require


def shards(c, *, action, algorithm, dataset, run, accounts, count, qps, workers, fund_wei, value_wei, cache_accounts=64, duration=0, max_inflight=None, slh_backend='noble'):
    hosts = [h for h in c['hosts'] if 'bench' in h['roles']]
    require(hosts, 'No bench hosts configured')
    require(action in ('prepare', 'create', 'transfer', 'reconcile'), 'Invalid action')
    require(math.isfinite(duration) and 0 <= duration <= 86400, 'Duration must be 0..86400 seconds')
    require(not duration or action in ('create', 'transfer'), 'Duration applies only to create/transfer')
    require(math.isfinite(qps) and 0 < qps <= 1_000_000, 'Invalid QPS')
    if count is None:
        count = math.ceil(qps * duration) + len(hosts) if duration and action == 'transfer' else accounts
    require(slh_backend in ('noble', 'openssl'), 'Invalid SLH backend')
    require(algorithm in ('ecdsa', 'ml', 'slh'), 'Use ecdsa, ml or slh')
    for name in (dataset, run):
        require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name), 'Invalid dataset/run name')
    require(accounts >= len(hosts) and count >= len(hosts), 'Need at least one account/operation per bench')
    require(accounts <= 10_000_000 and count <= 100_000_000, 'Workload too large')
    require(1 <= workers <= 64, 'Invalid workers')
    max_inflight = workers if max_inflight is None else max_inflight
    require(type(max_inflight) is int and 1 <= max_inflight <= 4096, 'Invalid max inflight')
    require(0 <= cache_accounts <= 4096, 'Invalid cache capacity')
    require(fund_wei.isdigit() and int(fund_wei) > 0 and value_wei.isdigit() and int(value_wei) > 0, 'Invalid wei amount')
    if action in ('prepare', 'create'):
        require(count == accounts, 'prepare/create count must equal accounts')
    def split(n, i):
        return n // len(hosts) + (i < n % len(hosts))
    return [(h, dict(action=action, algorithm=algorithm, dataset=dataset, run=run,
                     accounts=split(accounts, i), count=split(count, i), qps=qps / len(hosts),
                     workers=workers, maxInflight=max_inflight, slhBackend=slh_backend, cacheAccounts=cache_accounts, fundWei=fund_wei, valueWei=value_wei,
                     durationSeconds=duration, startAt=0))
            for i, h in enumerate(hosts)]


def execute(cluster, args):
    # No chain mutation for polling/stopping; keys remain exclusively on each client.
    hosts = [h for h in cluster.c['hosts'] if 'bench' in h['roles']]
    require(hosts, 'No bench hosts configured')
    if args.load_action == 'stop':
        cluster.control('stop', roles=['bench'])
        return
    if args.load_action == 'status':
        with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
            rows = list(pool.map(lambda h: cluster.call(h, 'load-status'), hosts))
        for h, row in zip(hosts, rows):
            progress = {k: v for k, v in row.get('progress', {}).items()
                        if k not in ('inclusions', 'inclusionTruncated')}
            print(h['id'] + ': ' + json.dumps({**row, 'progress': progress}))
        totals = {k: sum(r.get('progress', {}).get(k, 0) for r in rows)
                  for k in ('total', 'dispatched', 'attempted', 'accepted', 'confirmed', 'failed', 'skipped', 'pending', 'inflight', 'cancelled')}
        # Never combine unrelated runs or call acceptance rate confirmed TPS.
        runs = {(r.get('dataset'), r.get('run')) for r in rows}
        if len(runs) == 1 and all(None not in item for item in runs):
            snapshots = [r.get('progress', {}) for r in rows]
            if all(s.get('startedAt') and s.get('updatedAt') and s.get('status') != 'starting' for s in snapshots):
                elapsed = (max(s['updatedAt'] for s in snapshots) - min(s['startedAt'] for s in snapshots)) / 1000
                if elapsed > 0:
                    totals.update(elapsedSeconds=elapsed, acceptedPerSecond=totals['accepted'] / elapsed,
                                  confirmedPerSecond=totals['confirmed'] / elapsed)
            print('TOTAL: ' + json.dumps(totals))
        folder = cluster.home / 'load-results'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest = folder / 'latest.json'
        with open(dest, 'w') as stream:
            os.chmod(dest, 0o600)
            json.dump([{'host': h['id'], **r} for h, r in zip(hosts, rows)], stream, indent=2)
        return
    if args.load_action != 'reconcile':
        require(cluster.state.get('ready'), 'Complete deployment acceptance before load testing')
    plan = shards(cluster.c, action=args.load_action, algorithm=args.algorithm, dataset=args.dataset,
                  run=args.run, accounts=args.accounts, count=args.count, qps=args.qps, workers=args.workers,
                  fund_wei=args.fund_wei, value_wei=args.value_wei, cache_accounts=args.cache_accounts, duration=args.duration, max_inflight=args.max_inflight, slh_backend=args.slh_backend)
    ml_backend = getattr(args, 'ml_backend', 'process')
    require(ml_backend in ('process', 'persistent'), 'Invalid ML backend')
    require(ml_backend == 'process' or args.algorithm == 'ml', 'Persistent ML backend requires ML')
    for _, spec in plan:
        spec['mlBackend'] = ml_backend
    submission = getattr(args, 'submission', 'bundler')
    require(submission in ('bundler', 'direct'), 'Invalid submission mode')
    if submission == 'direct':
        require(args.load_action in ('transfer', 'reconcile') and args.algorithm in ('ml', 'slh'), 'Direct mode supports existing PQ transfers only')
        gas_file = getattr(args, 'direct_gas_file', None)
        require(gas_file is not None, 'Direct mode requires explicit gas limits')
        gas = json.loads(gas_file.read_text())
        require(isinstance(gas, dict), 'Direct gas limits must be an object')
        for k in ('callGasLimit', 'verificationGasLimit', 'preVerificationGas', 'outerGasLimit'):
            require(isinstance(gas.get(k), str) and gas[k].isdigit() and 0 < int(gas[k]) <= 16777216, 'Invalid direct gas limits')
        require(int(gas['outerGasLimit']) > sum(int(gas[k]) for k in ('callGasLimit', 'verificationGasLimit', 'preVerificationGas')), 'Outer gas needs execution headroom')
        for _, spec in plan:
            spec.update(submission='direct', directGas=gas)
        print('CHAIN CAPACITY CONTROL: direct handleOps; no Bundler simulation', flush=True)
    recipients_file = getattr(args, 'recipients_file', None)
    if recipients_file:
        require(args.load_action == 'transfer', 'Recipient pool applies only to transfers')
        addresses = json.loads(recipients_file.read_text())
        require(isinstance(addresses, list) and len(addresses) >= 2, 'Need at least two recipients')
        require(all(isinstance(a, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', a) and int(a, 16) for a in addresses), 'Invalid recipient address')
        require(len(set(a.lower() for a in addresses)) == len(addresses), 'Duplicate recipient')
        for _, spec in plan:
            spec['recipientAddresses'] = addresses
    # Configure all machines first, then arm a common future start time.
    start = int(time.time() * 1000) + 120_000
    for h, spec in plan:
        spec['startAt'] = start
        cluster.call(h, 'load-config', spec=spec)
    require(time.time() * 1000 < start - 30_000, 'SSM staging too slow; no services started, retry with a new run ID')
    try:
        for h, spec in plan:
            cluster.call(h, 'start', roles=['bench'])
            limit = f", {spec['durationSeconds']:g}s dispatch window" if spec['durationSeconds'] else ''
            print(f"{h['id']}: {spec['count']} operations max, {spec['qps']:.3f} target QPS, {spec['workers']} workers, {min(spec['maxInflight'], spec['accounts'], spec['count'])} max inflight{limit}")
    except Exception:
        cluster.control('stop', roles=['bench'])
        raise
    print('Clients prepare workers, then send at the scheduled time. Poll with load --load-action status.')
