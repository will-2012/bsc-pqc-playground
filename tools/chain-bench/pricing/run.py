#!/usr/bin/env python3
"""Measure actual BSC Run paths on an idle Linux x86_64 host; never start a chain."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import resource
import statistics
import subprocess
import time

CASES = [f'Pricing/{family}_{kind}' for family in ('ML', 'SLH')
         for kind in ('Valid', 'WrongMessage', 'Invalid', 'Zero', 'Random', 'Short', 'Long')]
CASES += ['PricingECDSA/ECRecover', 'PricingECDSA/Verify', 'PricingECDSA/Sign', 'PricingSign/ML', 'PricingSign/SLH']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse(text, name):
    match = re.search(r'^Benchmark' + re.escape(name) + r'(?:-\d+)?\s+(\d+)\s+([^\n]+)', text, re.M)
    if not match:
        raise ValueError('Missing benchmark: ' + name)
    metrics = {unit: float(value) for value, unit in re.findall(r'([\d.eE+-]+)\s+([\w/-]+)', match[2])}
    for key in ('ns/op', 'cpu-ns/op', 'thread-cpu-ns/op', 'B/op', 'allocs/op'):
        if key not in metrics or not math.isfinite(metrics[key]) or metrics[key] < 0:
            raise ValueError('Invalid benchmark metric: ' + key)
    if int(match[1]) < 1 or metrics['cpu-ns/op'] <= 0:
        raise ValueError('No measured operations')
    return dict(iterations=int(match[1]), metrics=metrics)


def summarize(rows):
    result = {}
    for name in CASES:
        group = [r['metrics'] for r in rows if r['name'] == name]
        result[name] = {'medianUs': statistics.median(x['cpu-ns/op'] for x in group) / 1000,
                        'maxRoundUs': max(x['cpu-ns/op'] for x in group) / 1000,
                        'minRoundUs': min(x['cpu-ns/op'] for x in group) / 1000,
                        'threadMedianUs': statistics.median(x['thread-cpu-ns/op'] for x in group) / 1000,
                        'wallMedianUs': statistics.median(x['ns/op'] for x in group) / 1000,
                        'bytesPerOp': statistics.median(x['B/op'] for x in group),
                        'allocsPerOp': statistics.median(x['allocs/op'] for x in group)}
    return result


def pricing(summary):
    baseline = summary['PricingECDSA/ECRecover']['medianUs']
    result = {}
    for family, gas in [('ML', 10000), ('SLH', 60000)]:
        paths = {name: value for name, value in summary.items() if name.startswith('Pricing/' + family + '_')}
        worst_name = max(paths, key=lambda n: paths[n]['maxRoundUs'])
        worst = paths[worst_name]['maxRoundUs']
        raw = 3000 * worst / baseline
        quote = math.ceil(raw * 1.25 / 1000) * 1000
        result[family] = dict(currentGas=gas, currentGasRatio=gas / 3000,
                              validCpuRatio=paths[f'Pricing/{family}_Valid']['medianUs'] / baseline,
                              worstCpuRatio=worst / baseline, worstPath=worst_name,
                              worstRoundMeanUs=worst, computeEquivalentGas=raw,
                              margin=1.25, rounded1000Gas=quote, rounded10000Gas=math.ceil(quote / 10000) * 10000,
                              currentCoverage=gas / raw)
    return result


def environment():
    cpu = Path('/proc/cpuinfo').read_text()
    first = {k.strip(): v.strip() for k, v in (line.split(':', 1) for line in cpu.split('\n\n')[0].splitlines() if ':' in line)}
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return dict(os='Linux', kernel=platform.release(), arch=platform.machine(),
                cpu=first['model name'].strip(), logicalCpus=os.cpu_count(),
                memoryKiB=int(memory['MemTotal'].split()[0]), availableMemoryKiB=int(memory['MemAvailable'].split()[0]),
                features=[f for f in ('avx2', 'avx512f', 'bmi2', 'sha_ni') if f in first.get('flags', '').split()],
                loadAverage=list(os.getloadavg()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', required=True, type=Path)
    p.add_argument('--fixtures', required=True, type=Path, help='Directory containing testdata/')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--backend', choices=['deployed-portable', 'mldsa-avx2'], required=True)
    p.add_argument('--rounds', type=int, default=7)
    p.add_argument('--cpu', type=int, required=True)
    p.add_argument('--gc', type=int, choices=[50, 100], default=100)
    args = p.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        p.error('Measure on Linux x86_64, without emulation')
    if args.rounds < 5 or args.cpu not in os.sched_getaffinity(0):
        p.error('Need >=5 rounds and an available CPU')
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    binary, cwd = args.binary.resolve(), args.fixtures.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    before = environment()
    comms = subprocess.check_output(['ps', '-eo', 'comm='], text=True).splitlines()
    if any(c.strip() in ('bsc', 'geth', 'bsc-pqc-validat', 'anvil') for c in comms):
        raise RuntimeError('Stop node processes before pricing measurements')
    os.sched_setaffinity(0, {args.cpu})
    env = {k: v for k, v in os.environ.items() if k not in ('GODEBUG', 'GOMEMLIMIT', 'GOGC', 'GOMAXPROCS')}
    env.update(GOMAXPROCS='1', GOGC=str(args.gc), LC_ALL='C')
    check = subprocess.check_output([str(binary), '-test.run=^(TestPricingPaths|TestPricingEnvironment|TestSLHV2Verify|TestMLDSAV4Verify)$', '-test.v'], cwd=cwd, env=env, text=True, stderr=subprocess.STDOUT, timeout=180)
    (args.output / 'checks.txt').write_text(check)
    flags = json.loads(re.search(r'PRICING_ENV (\{[^\n]+\})', check)[1])
    rows = []
    started = time.time()
    with (args.output / 'samples.txt').open('w') as log:
        for round_ in range(args.rounds):
            names = CASES.copy()
            random.Random(714 + round_).shuffle(names)
            for name in names:
                top, sub = name.split('/')
                out = subprocess.check_output([str(binary), '-test.run=^$', f'-test.bench=^Benchmark{top}$/^{sub}$', '-test.benchtime=1s', '-test.count=1', '-test.cpu=1', '-test.benchmem'], cwd=cwd, env=env, text=True, stderr=subprocess.STDOUT, timeout=180)
                log.write(f'ROUND {round_+1} {name}\n' + out); log.flush()
                row = dict(round=round_+1, name=name, **parse(out, name))
                rows.append(row)
                print(f'{args.backend} GC={args.gc} round={round_+1} {name}: {row["metrics"]["cpu-ns/op"]/1000:.3f} us', flush=True)
    summary = summarize(rows)
    data = dict(schema=1, backend=args.backend, rounds=args.rounds, cpu=args.cpu, gc=args.gc,
                environmentBefore=before, environmentAfter=environment(), runtime=flags,
                binarySha256=sha(binary), runnerSha256=sha(__file__),
                fixtureSha256={f.name: sha(f) for f in sorted((cwd / 'testdata').iterdir()) if f.is_file()},
                elapsedSeconds=time.time()-started, measurement='Process CPU per Run including Go GC; thread CPU/wall recorded; one pinned CPU; GOMAXPROCS=1; 1s per benchmark; seeded round order',
                rows=rows, summary=summary, pricing=pricing(summary))
    data['logSha256'] = sha(args.output / 'samples.txt')
    data['checksSha256'] = sha(args.output / 'checks.txt')
    (args.output / 'results.json').write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(data['pricing']), flush=True)


if __name__ == '__main__':
    main()
