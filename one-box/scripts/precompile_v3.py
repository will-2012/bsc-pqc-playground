"""Build the isolated Falcon v3 client; original v1/v2 builds are preserved."""
import importlib.util
import platform
import os
import json
import subprocess
import shutil
from pathlib import Path
from devnet import ROOT, BOX, BUILD, command, digest, read, write, binary_info
import precompile
WORK=BUILD/'v3'
SOURCE=WORK/'bsc'
BINARY=WORK/'geth'
NATIVE=BOX/'precompile/v3'

def prepare(work=WORK):
    source=work/'bsc'
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise RuntimeError('Pinned Falcon NEON backend currently validated only on macOS arm64')
    os.environ['CGO_CFLAGS_ALLOW']='-ffp-contract=off|-mcpu=apple-m1'
    print(f'Preparing {work.name} client with common PQ fork and SLH + Falcon...',flush=True)
    commit=precompile.prepare(work)
    log=work/'build.log'
    command(['patch','--batch','-p1','-i',BOX/'patches/bsc-pq-v3.patch'],log,source)
    # Reuse the benchmark source downloader/checksums, not its timing or unrelated backends.
    spec=importlib.util.spec_from_file_location('crypto_sources',ROOT/'tools/crypto-bench/run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.CACHE.mkdir(parents=True, exist_ok=True)
    backend=module.source('falcon-neon',read(NATIVE/'dependency.json'))/'falcon-armv8/neon'
    package=source/'crypto/pqfalcon';package.mkdir()
    shutil.copytree(NATIVE/'native',package,dirs_exist_ok=True)
    (package/'backend').mkdir()
    for h in list(backend.glob('*.h'))+list(backend.glob('*.c')):shutil.copy2(h,package/'backend'/h.name)
    # C translation units must be in the Go package directory for cgo to compile them.
    for name in module.FALCON_FILES:shutil.copy2(backend/(name+'.c'),package/('backend_'+name+'.c'))
    for p in NATIVE.glob('*.go'):shutil.copy2(p,source/'core/vm'/p.name)
    shutil.copy2(ROOT/'tools/crypto-bench/testdata/falcon.tsv',source/'core/vm/testdata/falcon_v3.tsv')
    # Existing SLH EVM constructor test also works on the v3 client's explicit fork.
    p=source/'core/vm/slh_v2_test.go';s=p.read_text();s=s.replace('constructed := NewEVM','full.PQTime = &zero\n\tconstructed := NewEVM');s=s.replace('evm := &EVM', 'activation := uint64(0)\n cfg.PQTime = &activation\n rules.IsPQ = true\n evm := &EVM');p.write_text(s)
    target=source/'cmd/pq-wallet';shutil.copytree(NATIVE/'wallet',target)
    changed=[source/p for p in ['params/config.go','cmd/utils/flags.go','cmd/geth/main.go','cmd/geth/config.go','cmd/geth/chaincmd.go','eth/backend.go','core/genesis.go','eth/ethconfig/config.go','eth/ethconfig/gen_config.go']]
    command(['gofmt','-w',*changed,*package.glob('*.go'),*list((source/'core/vm').glob('*v3*.go'))],log,source)
    return commit

def build():
    commit=prepare()
    log=WORK/'build.log'
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','Test(FalconV3|SLHV2)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./core/vm','-run','Test(SLHV2Disabled|FalconV3Disabled)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./params','-count=1'],log,SOURCE,timeout=300)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','^$','-fuzz','^FuzzFalconV3$','-fuzztime=5s','-parallel=1'],log,SOURCE,timeout=180)
    command(['go','build','-mod=readonly','-tags','pqprecompile','-buildvcs=false','-trimpath','-o',BINARY,'./cmd/geth'],log,SOURCE,timeout=1800)
    command(['go','build','-mod=readonly','-buildvcs=false','-trimpath','-o',WORK/'pq-wallet','./cmd/pq-wallet'],log,SOURCE,timeout=600)
    def wallet(payload):
        return json.loads(subprocess.check_output([WORK/'pq-wallet'],input=json.dumps(payload).encode()))
    key=wallet({'action':'keygen'})
    message='00'*32
    sig=wallet({'action':'sign','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'falcon-public.json',{'input':key['publicKey']+message+sig,'source':'pinned Falcon NEON native wallet'})
    del key
    info=binary_info(BINARY)
    info.update(source_commit=commit,build_tag='pqprecompile',
        build_script_sha256={p.name:digest(p) for p in [BOX/'scripts/precompile.py',BOX/'scripts/precompile_v3.py']},
        base_patch_sha256=digest(BOX/'patches/bsc-slh-v2.patch'),
        native_flags='-O3 -mcpu=apple-m1 -DFALCON_LOGN=9 -DAPPLE_M1=0 -fno-fast-math -ffp-contract=off',dependency=read(NATIVE/'dependency.json'),
        extension_files={str(p.relative_to(BOX)):digest(p) for d in [BOX/'precompile/v2',NATIVE] for p in d.rglob('*') if p.is_file()},
        patch_sha256=digest(BOX/'patches/bsc-pq-v3.patch'),wallet_sha256=digest(WORK/'pq-wallet'))
    write(WORK/'binary.json',info)
    print(f'Built {BINARY} and native wallet helper',flush=True)
