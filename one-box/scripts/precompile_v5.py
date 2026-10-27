"""Build ML-DSA-65 v5 alongside previous PQ precompiles, under one PQ fork."""
import shutil
import json
import subprocess
from devnet import ROOT,BOX,BUILD,command,read,write,digest,binary_info
import precompile_v4
WORK=BUILD/'v5'
SOURCE=WORK/'bsc'
BINARY=WORK/'geth'
NATIVE=BOX/'precompile/v5'

def build():
    dependency=read(NATIVE/'dependency.json')
    base=read(BOX/'precompile/v4/dependency.json')
    if any(dependency[k]!=v for k,v in base.items()):
        raise RuntimeError('v4/v5 must use the same pinned mldsa-native backend')
    if digest(NATIVE/'testdata/mldsa65.tsv')!=dependency['public_kat']['sha256']:
        raise RuntimeError('ML-DSA-65 public KAT checksum mismatch')
    commit=precompile_v4.prepare(WORK,levels=(44,65))
    log=WORK/'build.log'
    package=SOURCE/'crypto/pqmldsa'
    shutil.copytree(NATIVE/'native',package,dirs_exist_ok=True)
    shutil.copy2(WORK/'mldsa/test/build/libmldsa65.a',package/'libmldsa65.a')
    for p in NATIVE.glob('*.go'):shutil.copy2(p,SOURCE/'core/vm'/p.name)
    shutil.copy2(NATIVE/'testdata/mldsa65.tsv',SOURCE/'core/vm/testdata/mldsa_v5.tsv')
    p=SOURCE/'core/vm/evm.go';p.write_text(p.read_text().replace('v4Precompiles(', 'v5Precompiles('))
    shutil.copytree(NATIVE/'wallet',SOURCE/'cmd/pq-wallet',dirs_exist_ok=True)
    command(['gofmt','-w',*package.glob('*.go'),*list((SOURCE/'core/vm').glob('*v5*.go'))],log,SOURCE)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','Test(MLDSAV5|MLDSAV4|FalconV3|SLHV2)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./core/vm','-run','Test(SLHV2Disabled|FalconV3Disabled|MLDSAV4Disabled|MLDSAV5Disabled)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./params','-count=1'],log,SOURCE,timeout=300)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','^$','-fuzz','^FuzzMLDSAV5$','-fuzztime=5s','-parallel=1'],log,SOURCE,timeout=180)
    command(['go','build','-mod=readonly','-tags','pqprecompile','-buildvcs=false','-trimpath','-o',BINARY,'./cmd/geth'],log,SOURCE,timeout=1800)
    command(['go','build','-mod=readonly','-buildvcs=false','-trimpath','-o',WORK/'pq-wallet','./cmd/pq-wallet'],log,SOURCE,timeout=600)
    def wallet(payload):return json.loads(subprocess.check_output([WORK/'pq-wallet'],input=json.dumps(payload).encode()))
    key=wallet({'action':'keygen'});message='00'*32
    sig=wallet({'action':'sign','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'mldsa-public.json',{'input':key['publicKey']+message+sig,'source':'pinned mldsa-native Pure ML-DSA-65, empty context'})
    del key
    key=wallet({'action':'keygen','algorithm':'mldsa44'})
    sig=wallet({'action':'sign','algorithm':'mldsa44','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'mldsa44-public.json',{'input':key['publicKey']+message+sig,'source':'pinned ML-DSA-44 native wallet'})
    del key
    key=wallet({'action':'keygen','algorithm':'falcon'})
    sig=wallet({'action':'sign','algorithm':'falcon','secretKey':key['secretKey'],'message':message})['signature']
    write(WORK/'falcon-public.json',{'input':key['publicKey']+message+sig,'source':'pinned Falcon native wallet'})
    del key
    info=binary_info(BINARY)
    info.update(source_commit=commit,build_tag='pqprecompile',dependency=read(NATIVE/'dependency.json'),
      native_flags='clang -O3 -mcpu=apple-m1 OPT=1 AUTO=1; Pure ML-DSA-44/65 empty context',
      static_library_sha256={str(p.name):digest(p) for p in package.glob('*.a')},wallet_sha256=digest(WORK/'pq-wallet'),
      extension_files={str(p.relative_to(BOX)):digest(p) for v in [2,3,4,5] for p in (BOX/f'precompile/v{v}').rglob('*') if p.is_file()},
      build_scripts={p.name:digest(p) for p in [BOX/'scripts/precompile.py',BOX/'scripts/precompile_v3.py',BOX/'scripts/precompile_v4.py',BOX/'scripts/precompile_v5.py']},
      patches={p.name:digest(p) for p in [BOX/'patches/bsc-slh-v2.patch',BOX/'patches/bsc-pq-v3.patch']})
    write(WORK/'binary.json',info)
    print(f'Built {BINARY} and ML-DSA-65 native wallet',flush=True)
