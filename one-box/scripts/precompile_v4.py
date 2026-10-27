"""Add ML-DSA-44 to the common PQ fork in an isolated v4 client."""
import os
import shutil
import subprocess
import json
import importlib.util
from devnet import ROOT,BOX,BUILD,command,read,write,digest,binary_info
import precompile_v3
WORK=BUILD/'v4'
SOURCE=WORK/'bsc'
BINARY=WORK/'geth'
NATIVE=BOX/'precompile/v4'

def prepare(work=WORK,levels=(44,)):
    source=work/'bsc'
    commit=precompile_v3.prepare(work)
    log=work/'build.log'
    spec=importlib.util.spec_from_file_location('crypto_sources',ROOT/'tools/crypto-bench/run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    upstream=module.source('mldsa',read(NATIVE/'dependency.json'))
    ml=work/'mldsa'
    if ml.exists():shutil.rmtree(ml)
    shutil.copytree(upstream,ml,ignore=shutil.ignore_patterns('build','.git'))
    previous_cflags=os.environ.get('CFLAGS')
    os.environ['CFLAGS']='-O3 -mcpu=apple-m1'
    try:
        command(['make','-j4','CC=clang','OPT=1','AUTO=1',*[f'test/build/libmldsa{level}.a' for level in levels]],log,ml,timeout=600)
        command(['make','CC=clang','OPT=1','AUTO=1',*[target for level in levels for target in [f'run_func_{level}',f'run_kat_{level}']]],log,ml,timeout=300)
    finally:
        if previous_cflags is None:os.environ.pop('CFLAGS',None)
        else:os.environ['CFLAGS']=previous_cflags
    package=source/'crypto/pqmldsa'
    shutil.copytree(NATIVE/'native',package)
    shutil.copytree(ml/'mldsa',package/'backend')
    shutil.copy2(ml/'test/build/libmldsa44.a',package/'libmldsa44.a')
    for p in NATIVE.glob('*.go'):shutil.copy2(p,source/'core/vm'/p.name)
    shutil.copy2(ROOT/'tools/crypto-bench/testdata/standard.tsv',source/'core/vm/testdata/mldsa_v4.tsv')
    # All three entries use the existing PQ timestamp; v3 snapshot stays unchanged.
    p=source/'core/vm/evm.go';p.write_text(p.read_text().replace('v3Precompiles(', 'v4Precompiles('))
    shutil.copytree(NATIVE/'wallet',source/'cmd/pq-wallet',dirs_exist_ok=True)
    command(['gofmt','-w',*package.glob('*.go'),*list((source/'core/vm').glob('*v4*.go'))],log,source)
    return commit

def build():
    commit=prepare()
    package=SOURCE/'crypto/pqmldsa'
    log=WORK/'build.log'
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','Test(MLDSAV4|FalconV3|SLHV2)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./core/vm','-run','Test(SLHV2Disabled|FalconV3Disabled|MLDSAV4Disabled)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./params','-count=1'],log,SOURCE,timeout=300)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','^$','-fuzz','^FuzzMLDSAV4$','-fuzztime=5s','-parallel=1'],log,SOURCE,timeout=180)
    command(['go','build','-mod=readonly','-tags','pqprecompile','-buildvcs=false','-trimpath','-o',BINARY,'./cmd/geth'],log,SOURCE,timeout=1800)
    command(['go','build','-mod=readonly','-buildvcs=false','-trimpath','-o',WORK/'pq-wallet','./cmd/pq-wallet'],log,SOURCE,timeout=600)
    def wallet(payload):return json.loads(subprocess.check_output([WORK/'pq-wallet'],input=json.dumps(payload).encode()))
    key=wallet({'action':'keygen'});message='00'*32
    sig=wallet({'action':'sign','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'mldsa-public.json',{'input':key['publicKey']+message+sig,'source':'pinned mldsa-native Pure ML-DSA-44, empty context'})
    del key
    key=wallet({'action':'keygen','algorithm':'falcon'})
    sig=wallet({'action':'sign','algorithm':'falcon','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'falcon-public.json',{'input':key['publicKey']+message+sig,'source':'pinned Falcon native wallet'})
    del key
    info=binary_info(BINARY)
    info.update(source_commit=commit,build_tag='pqprecompile',dependency=read(NATIVE/'dependency.json'),
      native_flags='clang -O3 -mcpu=apple-m1 OPT=1 AUTO=1; Pure ML-DSA-44 empty context',
      static_library_sha256=digest(package/'libmldsa44.a'),wallet_sha256=digest(WORK/'pq-wallet'),
      extension_files={str(p.relative_to(BOX)):digest(p) for v in [2,3,4] for p in (BOX/f'precompile/v{v}').rglob('*') if p.is_file()},
      build_scripts={p.name:digest(p) for p in [BOX/'scripts/precompile.py',BOX/'scripts/precompile_v3.py',BOX/'scripts/precompile_v4.py']},
      patches={p.name:digest(p) for p in [BOX/'patches/bsc-slh-v2.patch',BOX/'patches/bsc-pq-v3.patch']})
    write(WORK/'binary.json',info)
    print(f'Built {BINARY} and ML-DSA native wallet',flush=True)
