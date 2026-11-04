#!/usr/bin/env python3
"""Build an isolated test binary from the already-built cluster client."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CLUSTER = ROOT / 'one-box/.runtime/build/cluster'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--optimized-ml', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        parser.error('Build on Linux x86_64')
    source = CLUSTER / 'bsc'
    if not (source / 'core/vm/slh_v2.go').exists():
        parser.error('Build the cluster client first')
    info = json.loads((CLUSTER / 'binary.json').read_text())
    lib = source / 'crypto/pqmldsa/libmldsa44.a'
    if sha(lib) != info['library_sha256']:
        parser.error('Deployed ML library differs from build manifest')
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error('Output must be outside the client source tree')
    output.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items() if k not in ('GOFLAGS', 'CGO_CFLAGS', 'CGO_LDFLAGS', 'GODEBUG', 'GOGC', 'GOMEMLIMIT', 'CFLAGS')}
    env.update(GOAMD64='v1', CGO_ENABLED='1')
    work = output / 'source'
    # Hardlinks save disk; replacement below unlinks first and never changes the
    # original client sources or its static library.
    shutil.copytree(source, work, copy_function=os.link, ignore=shutil.ignore_patterns('.git', 'pricing_test.go'))
    shutil.copy2(Path(__file__).with_name('pricing_test.go'), work / 'core/vm/pricing_test.go')
    ml_description = 'portable C OPT=0 AUTO=0; -O3 -fno-fast-math'
    with (output / 'build.log').open('w') as log:
        def run(command, cwd=work, extra=None):
            subprocess.run(list(map(str, command)), cwd=cwd, env={**env, **(extra or {})}, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        if args.optimized_ml:
            flags = set(Path('/proc/cpuinfo').read_text().split())
            if not {'avx2', 'bmi2', 'popcnt'} <= flags:
                raise RuntimeError('AVX2, BMI2 and POPCNT required for optimized build and tests')
            dependency = json.loads((ROOT / 'one-box/precompile/v4/dependency.json').read_text())
            if (CLUSTER / 'mldsa/.extracted').read_text().strip() != dependency['commit']:
                raise RuntimeError('ML source cache does not match pinned dependency')
            ml = output / 'mldsa'
            shutil.copytree(CLUSTER / 'mldsa', ml, ignore=shutil.ignore_patterns('.git'))
            cflags = '-O3 -mavx2 -mbmi2 -mpopcnt -mtune=generic -fno-fast-math'
            run(['make', 'clean'], ml, {'CFLAGS': cflags})
            run(['make', '-j4', 'CC=cc', 'OPT=1', 'AUTO=1', 'test/build/libmldsa44.a', 'run_func_44', 'run_kat_44'], ml, {'CFLAGS': cflags})
            target = work / 'crypto/pqmldsa/libmldsa44.a'
            target.unlink()
            shutil.copy2(ml / 'test/build/libmldsa44.a', target)
            ml_description = 'OPT=1 AUTO=1; ' + cflags
        run(['gofmt', '-w', 'core/vm/pricing_test.go'])
        run(['go', 'test', '-mod=readonly', '-tags', 'pqprecompile', '-buildvcs=false', '-trimpath', '-c', '-o', output / 'vm.test', './core/vm'])
    data = dict(upstreamCommit=info['source_commit'], deployedClientSha256=info['sha256'],
                mlBuild=ml_description, mlLibrarySha256=sha(work / 'crypto/pqmldsa/libmldsa44.a'),
                binarySha256=sha(output / 'vm.test'), benchmarkSourceSha256=sha(work / 'core/vm/pricing_test.go'),
                goBuild=['-mod=readonly', '-tags=pqprecompile', '-buildvcs=false', '-trimpath', 'GOAMD64=v1', 'CGO_ENABLED=1'],
                goEnvironment=json.loads(subprocess.check_output(['go', 'env', '-json', 'GOVERSION', 'GOOS', 'GOARCH', 'CGO_CFLAGS'], cwd=work, env=env, text=True)),
                compiler=subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
                runSourceSha256={name: sha(work / name) for name in ['core/vm/slh_v2.go', 'core/vm/mldsa_v4.go', 'crypto/pqmldsa/mldsa.go', 'crypto/pqmldsa/bridge.c']},
                dependencies={v: json.loads((ROOT / 'one-box/precompile' / v / 'dependency.json').read_text()) for v in ['v2', 'v4']})
    (output / 'build.json').write_text(json.dumps(data, indent=2) + '\n')
    (output / 'testdata').mkdir()
    for name in ['slh_v2.json', 'mldsa_v4.tsv']:
        shutil.copy2(work / 'core/vm/testdata' / name, output / 'testdata' / name)
    print('Pricing binary built; client and chain unchanged')


if __name__ == '__main__':
    main()
