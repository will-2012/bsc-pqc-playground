"""Controller-side partitioning. Contains no machine identity or key material."""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from model import require


def shards(c, *, action, algorithm, dataset, run, accounts, count, qps, workers, fund_wei, value_wei, cache_accounts=64):
    hosts = [h for h in c['hosts'] if 'bench' in h['roles']]
    require(hosts, 'No bench hosts configured')
    require(action in ('prepare', 'create', 'transfer', 'reconcile'), 'Invalid action')
    require(algorithm in ('ecdsa', 'ml', 'slh'), 'Use ecdsa, ml or slh')
    for name in (dataset, run):
        require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name), 'Invalid dataset/run name')
    require(accounts >= len(hosts) and count >= len(hosts), 'Need at least one account/operation per bench')
    require(accounts <= 10_000_000 and count <= 100_000_000, 'Workload too large')
    require(0 < qps <= 1_000_000 and 1 <= workers <= 64, 'Invalid QPS/workers')
    require(0 <= cache_accounts <= 4096, 'Invalid cache capacity')
    require(fund_wei.isdigit() and int(fund_wei) > 0 and value_wei.isdigit() and int(value_wei) > 0, 'Invalid wei amount')
    if action in ('prepare', 'create'):
        require(count == accounts, 'prepare/create count must equal accounts')
    def split(n, i):
        return n // len(hosts) + (i < n % len(hosts))
    return [(h, dict(action=action, algorithm=algorithm, dataset=dataset, run=run,
                     accounts=split(accounts, i), count=split(count, i), qps=qps / len(hosts),
                     workers=workers, cacheAccounts=cache_accounts, fundWei=fund_wei, valueWei=value_wei, startAt=0))
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
            print(h['id'] + ': ' + json.dumps(row))
        totals = {k: sum(r.get('progress', {}).get(k, 0) for r in rows)
                  for k in ('total', 'dispatched', 'attempted', 'accepted', 'confirmed', 'failed', 'skipped', 'pending', 'inflight')}
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
    count = args.count if args.count is not None else args.accounts
    plan = shards(cluster.c, action=args.load_action, algorithm=args.algorithm, dataset=args.dataset,
                  run=args.run, accounts=args.accounts, count=count, qps=args.qps, workers=args.workers,
                  fund_wei=args.fund_wei, value_wei=args.value_wei, cache_accounts=args.cache_accounts)
    # Configure all machines first, then arm a common future start time.
    start = int(time.time() * 1000) + 120_000
    for h, spec in plan:
        spec['startAt'] = start
        cluster.call(h, 'load-config', spec=spec)
    require(time.time() * 1000 < start - 30_000, 'SSM staging too slow; no services started, retry with a new run ID')
    try:
        for h, spec in plan:
            cluster.call(h, 'start', roles=['bench'])
            print(f"{h['id']}: {spec['count']} operations, {spec['qps']:.3f} target QPS, {spec['workers']} workers")
    except Exception:
        cluster.control('stop', roles=['bench'])
        raise
    print('Clients prepare workers, then send at the scheduled time. Poll with load --load-action status.')
