"""Build SLH-DSA-SHA2-128f v6 under the common PQ fork."""
import shutil
import json
import subprocess
from devnet import ROOT,BOX,BUILD,command,read,write,digest,binary_info
import precompile_v5
WORK=BUILD/'v6'
SOURCE=WORK/'bsc'
BINARY=WORK/'geth'
NATIVE=BOX/'precompile/v6'

def build():
    if read(NATIVE/'dependency.json')!=read(BOX/'precompile/v2/dependency.json'):
        raise RuntimeError('SLH128s/128f must use the same pinned Go backend')
    commit=precompile_v5.prepare(WORK)
    log=WORK/'build.log'
    package=SOURCE/'crypto/pqmldsa'
    for p in NATIVE.glob('*.go'):shutil.copy2(p,SOURCE/'core/vm'/p.name)
    shutil.copy2(ROOT/'tools/crypto-bench/testdata/standard.tsv',SOURCE/'core/vm/testdata/slh_v6.tsv')
    p=SOURCE/'core/vm/evm.go';p.write_text(p.read_text().replace('v5Precompiles(', 'v6Precompiles('))
    command(['gofmt','-w',*list((SOURCE/'core/vm').glob('*v6*.go'))],log,SOURCE)
    command(['go','test','-mod=readonly','github.com/trailofbits/go-slh-dsa/slh_dsa','-run','^TestKatSigVer$','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','Test(SLHV6|MLDSAV5|MLDSAV4|FalconV3|SLHV2)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./core/vm','-run','Test(SLHV2Disabled|FalconV3Disabled|MLDSAV4Disabled|MLDSAV5Disabled|SLHV6Disabled)','-count=1'],log,SOURCE,timeout=600)
    command(['go','test','-mod=readonly','./params','-count=1'],log,SOURCE,timeout=300)
    command(['go','test','-mod=readonly','-tags','pqprecompile','./core/vm','-run','^$','-fuzz','^FuzzSLHV6$','-fuzztime=5s','-parallel=1'],log,SOURCE,timeout=180)
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
    command(['node',ROOT/'tools/chain-bench/v6/vector.mjs'],log,ROOT)
    info=binary_info(BINARY)
    info.update(source_commit=commit,build_tag='pqprecompile',dependency=read(NATIVE/'dependency.json'),
      wallet_sha256=digest(WORK/'pq-wallet'),static_library_sha256={p.name:digest(p) for p in package.glob('*.a')},
      extension_files={str(p.relative_to(BOX)):digest(p) for v in [2,3,4,5,6] for p in (BOX/f'precompile/v{v}').rglob('*') if p.is_file()},
      build_scripts={p.name:digest(p) for p in [BOX/'scripts/precompile.py',BOX/'scripts/precompile_v3.py',BOX/'scripts/precompile_v4.py',BOX/'scripts/precompile_v5.py',BOX/'scripts/precompile_v6.py']},
      patches={p.name:digest(p) for p in [BOX/'patches/bsc-slh-v2.patch',BOX/'patches/bsc-pq-v3.patch']})
    write(WORK/'binary.json',info)
    print(f'Built {BINARY}; SLH128f shares original SLH backend',flush=True)
