#!/usr/bin/env python3
"""One entry: build pinned native backends, run the common Go API, verify, report."""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = HERE / '.runtime'
DOCS = ROOT / 'docs/crypto-bench'
NAMES = ['ECDSA-secp256k1', 'Falcon-512', 'ML-DSA-44', 'ML-DSA-65', 'SLH-DSA-SHA2-128s', 'SLH-DSA-SHA2-128f']
SIZES = [(32,33,64,64),(1280,896,617,667),(2560,1312,2420,2420),(4032,1952,3309,3309),(64,32,7856,7856),(64,32,17088,17088)]
PROFILES = {
    'macos-arm64': {'os':'darwin','arch':'arm64','cc':'clang','goos':'darwin','goarch':'arm64',
                     'flags':['-O3','-mcpu=apple-m1','-DNDEBUG','-fno-fast-math','-ffp-contract=off'], 'validated':True},
    'linux-x86_64': {'os':'linux','arch':'x86_64','cc':'gcc','goos':'linux','goarch':'amd64',
                     'flags':['-O3','-march=native','-mtune=native','-DNDEBUG','-fno-fast-math','-ffp-contract=off'], 'validated':False},
}
SOURCE_FILES = ['run.py','main.go','native.c','native.h','mldsa.c','falcon.c','go.mod','sources.json','testdata/falcon.tsv','testdata/standard.tsv']
FALCON_FILES = 'codec util common fft fft_tree fpr keygen rng poly_float sampler shake sign vrfy ntt ntt_consts poly_int falcon'.split()

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
def read(path): return json.loads(Path(path).read_text())
def output(args, cwd=ROOT): return subprocess.check_output(args,cwd=cwd,text=True,stderr=subprocess.STDOUT).strip()
def detect_profile(request='auto'):
    detected = next((name for name,p in PROFILES.items() if p['os']==sys.platform and p['arch']==platform.machine()),None)
    if request!='auto' and request!=detected: raise RuntimeError(f'Requested {request}, actual host is {sys.platform}/{platform.machine()}; cross-platform timing is forbidden.')
    if detected is None: raise RuntimeError('Unsupported host; no benchmark was run.')
    if not PROFILES[detected]['validated']: raise RuntimeError('Linux x86_64 has a separate profile, but its Falcon backend has not been integrated/validated yet. Do not compile or time the ARM backend here.')
    return detected

def no_devnet():
    found=[]
    for line in output(['ps','-axo','comm,args']).splitlines()[1:]:
        comm=line.split()[0]
        if Path(comm).name in ('geth','anvil') or re.search(r'\bnode\b.*(?:alto/(?:src|dist)|alto.*cli|bundler.*\.js)',line):found.append(line)
    if found: raise RuntimeError('Stop devnet/bundler before timing:\n'+'\n'.join(found))

def environment(profile):
    def optional(args):
        try:return output(args)
        except (subprocess.SubprocessError,OSError):return 'unavailable'
    return {'profile':profile,'os':optional(['sw_vers']),'architecture':platform.machine(),
            'cpu':optional(['sysctl','-n','machdep.cpu.brand_string']),
            'physical_cores':optional(['sysctl','-n','hw.physicalcpu']),
            'memory_bytes':optional(['sysctl','-n','hw.memsize']),
            'go':optional(['go','version']),'compiler':optional([PROFILES[profile]['cc'],'--version']),
            'power':optional(['pmset','-g','batt']),'thermal':optional(['pmset','-g','therm']),
            'load_average':optional(['sysctl','-n','vm.loadavg']),
            'cpu_usage':optional(['top','-l','2','-n','0','-s','1']),
            'affinity':'Go runtime.LockOSThread + GOMAXPROCS(1); user-initiated QoS; no CPU-core pin or locked frequency'}

def source(name,spec):
    archive=CACHE/(name+'.tar.gz')
    if not archive.exists() or sha(archive)!=spec['sha256']:
        print('Download',name,flush=True)
        tmp=archive.with_suffix('.part')
        with urllib.request.urlopen(spec['url'],timeout=120) as r,tmp.open('wb') as f:shutil.copyfileobj(r,f)
        if sha(tmp)!=spec['sha256']:raise RuntimeError('Source checksum mismatch: '+name)
        tmp.replace(archive)
    dest=CACHE/'src'/name;marker=dest/'.extracted'
    if not marker.exists() or marker.read_text().strip()!=spec['commit']:
        shutil.rmtree(dest,ignore_errors=True);dest.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                pieces=member.name.split('/',1)
                if len(pieces)==2 and pieces[1]:
                    member.name=pieces[1];tar.extract(member,dest,filter='data')
        marker.write_text(spec['commit']+'\n')
    return dest

def build(profile):
    work=CACHE/profile;work.mkdir(parents=True,exist_ok=True)
    cfg=PROFILES[profile];flags=cfg['flags'];lock=read(HERE/'sources.json')
    identities={'profile':profile,'flags':flags,'go':output(['go','version']),'cc':output([cfg['cc'],'--version']),
                'sources':lock,'files':{name:sha(HERE/name) for name in SOURCE_FILES}}
    stamp=work/'build.json';binary=work/'bench'
    if stamp.exists():
        cached=read(stamp)
        if cached['identity']==identities and all(Path(p).exists() and sha(p)==h for p,h in cached['artifacts'].items()):return cached
    assert sha(HERE/'testdata/falcon.tsv')==lock['falcon-neon']['public_kat']['fixture_sha256']
    assert sha(HERE/'testdata/standard.tsv')==lock['openssl']['public_vectors']['sha256']
    paths={name:source(name,spec) for name,spec in lock.items()}
    commands=[];log=work/'checks.txt';log.write_text('')
    clean_env={k:v for k,v in os.environ.items() if not k.startswith(('OPENSSL_','DYLD_','CGO_','GOFLAGS','CFLAGS','CXXFLAGS','CPPFLAGS','LDFLAGS','GODEBUG','GOMEMLIMIT','GOGC'))}
    clean_env.update({'LC_ALL':'C','OPENSSL_CONF':'/dev/null','CC':cfg['cc']})
    def run(args,cwd=ROOT,env=None):
        args=list(map(str,args));commands.append({'argv':args,'cwd':str(cwd),'env':env or {}})
        print('+',' '.join(args[:5]),flush=True)
        with log.open('a') as f:
            f.write('\n$ '+' '.join(args)+'\n');f.flush()
            subprocess.run(args,cwd=cwd,env={**clean_env,**(env or {})},stdout=f,stderr=subprocess.STDOUT,check=True)
    openssl,secp,ml,fn=(paths[n] for n in ['openssl','secp256k1','mldsa','falcon-neon'])
    # Cache by actual library source, compiler and flags, not report annotations.
    dep_identity={'profile':profile,'flags':flags,'compiler':identities['cc'],
                  'sources':{n:{k:lock[n][k] for k in ['commit','sha256']} for n in ['openssl','secp256k1','mldsa']}}
    dep_path=work/'native-dependencies.json'
    dependencies=read(dep_path) if dep_path.exists() else None
    reuse=bool(dependencies and dependencies['identity']==dep_identity and
               all(Path(p).exists() and sha(p)==h for p,h in dependencies['artifacts'].items()))
    secplib=work/'secp/lib/libsecp256k1.a'
    if reuse:
        log.write_text('Reused validated native dependencies.\n'+dependencies['checks'])
    else:
        if (openssl/'Makefile').exists():run(['make','clean'],openssl)
        shutil.rmtree(work/'secp',ignore_errors=True)
        run(['perl','Configure','darwin64-arm64-cc','no-shared','no-module','no-apps','no-docs','--libdir=lib',*flags],openssl)
        run(['make','-j4','build_libs'],openssl)
        run(['cmake','-S',secp,'-B',work/'secp','-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF','-DCMAKE_C_COMPILER='+cfg['cc'],'-DCMAKE_C_FLAGS_RELEASE='+' '.join(flags),'-DSECP256K1_BUILD_BENCHMARK=OFF','-DSECP256K1_BUILD_EXHAUSTIVE_TESTS=OFF','-DSECP256K1_BUILD_CTIME_TESTS=OFF'])
        run(['cmake','--build',work/'secp','-j4']);run(['ctest','--test-dir',work/'secp','--output-on-failure'])
        run(['make','clean'],ml)
        run(['make','-j4','CC='+cfg['cc'],'OPT=1','AUTO=1','test/build/libmldsa44.a','test/build/libmldsa65.a'],ml,{'CFLAGS':' '.join(flags)})
        run(['make','CC='+cfg['cc'],'OPT=1','AUTO=1','run_func_44','run_kat_44','run_func_65','run_kat_65'],ml,{'CFLAGS':' '.join(flags)})
        deps=[openssl/'libcrypto.a',secplib,ml/'test/build/libmldsa44.a',ml/'test/build/libmldsa65.a']
        dependencies={'identity':dep_identity,'commands':commands.copy(),'artifacts':{str(p):sha(p) for p in deps},'checks':log.read_text()}
        save(dep_path,dependencies)
    neon=fn/'falcon-armv8/neon';objects=[]
    for name in FALCON_FILES:
        obj=work/('falcon_'+name+'.o');objects.append(obj)
        run([cfg['cc'],*flags,'-DFALCON_LOGN=9','-DAPPLE_M1=0','-Dfalcon_verify=falcon_neon_verify','-I'+str(neon),'-c',neon/(name+'.c'),'-o',obj])
    falconlib=work/'libfalcon.a';run(['ar','rcs',falconlib,*objects])
    upstream_test=work/'falcon-upstream-test'
    run([cfg['cc'],*flags,'-DFALCON_LOGN=9','-DAPPLE_M1=0','-Dfalcon_verify=falcon_neon_verify','-I'+str(neon),neon/'test_falcon.c',falconlib,'-o',upstream_test])
    run([upstream_test])
    includes=[openssl/'include',secp/'include',ml/'mldsa',neon]
    cflags=flags+['-DFALCON_LOGN=9','-DAPPLE_M1=0']+['-I'+str(p) for p in includes]
    libraries=[falconlib,ml/'test/build/libmldsa44.a',ml/'test/build/libmldsa65.a',openssl/'libcrypto.a',secplib]
    mltest=work/'ml-interop-test'
    run([cfg['cc'],*cflags,'-DML_WRAPPER_TEST_MAIN',HERE/'mldsa.c',*libraries[1:4],'-framework','Security','-framework','CoreFoundation','-o',mltest])
    run([mltest])
    # Upstream fixed-seed helper is a paper benchmark stub; our adapter bypasses
    # it and its own test verifies independent keygen + normal OS randomness.
    test=work/'falcon-test'
    run([cfg['cc'],*cflags,'-DFALCON_WRAPPER_TEST_MAIN',HERE/'falcon.c',falconlib,'-o',test])
    run([test,HERE/'testdata/falcon.tsv'])
    run(['go','build','-trimpath','-o',binary,'.'],HERE,{'CGO_ENABLED':'1','GOOS':cfg['goos'],'GOARCH':cfg['goarch'],
        'CGO_CFLAGS':' '.join(cflags),'CGO_LDFLAGS':' '.join(map(str,libraries))+' -framework Security -framework CoreFoundation'})
    run([binary,'--kat',HERE/'testdata/standard.tsv'])
    metadata={'identity':identities,'commands':commands,'artifacts':{str(p):sha(p) for p in [binary,*libraries]},
              'built_at':dt.datetime.now(dt.timezone.utc).isoformat(),'checks_sha256':sha(log)}
    metadata['native_dependencies']=dependencies
    metadata['checks_sha256']=sha(log);save(stamp,metadata);return metadata

def corpus():
    messages=[bytes(32),bytes([255])*32,bytes(range(32)),bytes(reversed(range(32)))]
    messages += [hashlib.sha256(b'bsc-pqc-playground/crypto-bench/corpus-v1/'+i.to_bytes(8,'big')).digest() for i in range(4092)]
    path=CACHE/'corpus.bin';path.write_bytes(b''.join(messages));return path

def aggregate(data):
    rows=data['rows'];cfg=data['config'];result=[]
    for index,name in enumerate(NAMES):
        checks=[r for r in rows if r['kind']=='check' and r['algorithm']==name]
        assert len(checks)==cfg['rounds'] and {r['round'] for r in checks}==set(range(cfg['rounds']))
        for r in checks:
            assert (r['private_key_bytes'],r['public_key_bytes'],r['signature_min_bytes'],r['signature_max_bytes'])==SIZES[index]
            assert r['vectors']==32 and r['checks_per_vector']==5
            assert r['order']==(index-r['round']%len(NAMES)+len(NAMES))%len(NAMES)
        item={'algorithm':name,'private_key_bytes':SIZES[index][0],'public_key_bytes':SIZES[index][1],
              'signature_min_bytes':SIZES[index][2],'signature_max_bytes':SIZES[index][3],
              'setup_us':statistics.median(r['key_setup_cpu_ns']/1000 for r in checks)}
        for op in ['sign','verify']:
            timed=[r for r in rows if r['kind']=='timing' and r['algorithm']==name and r['operation']==op]
            assert len(timed)==cfg['rounds']*cfg['samples']
            assert {(r['round'],r['sample']) for r in timed}=={(r,s) for r in range(cfg['rounds']) for s in range(cfg['samples'])}
            for r in timed:
                assert r['iterations']==cfg[op+'_count'] and r['cpu_ns']>0 and r['wall_ns']>0
                offset=r['sample']*cfg['sign_count'] if op=='sign' else cfg['samples']*cfg['sign_count']-32
                assert r['message_start_index']==(r['round']*cfg['samples']*cfg['sign_count']+offset)%4096
                assert SIZES[index][2]<=r['signature_observed_min_bytes']<=r['signature_observed_max_bytes']<=SIZES[index][3]
                assert r['order']==(index-r['round']%len(NAMES)+len(NAMES))%len(NAMES)
            values={field:[statistics.median(r[field]/r['iterations']/1000 for r in timed if r['round']==k) for k in range(cfg['rounds'])] for field in ['cpu_ns','wall_ns','process_cpu_ns']}
            item[op]={'cpu_us':statistics.median(values['cpu_ns']),'wall_us':statistics.median(values['wall_ns']),
                      'process_us':statistics.median(values['process_cpu_ns']),'round_cpu_us':values['cpu_ns']}
        result.append(item)
    assert len(rows)==len(NAMES)*cfg['rounds']*(1+2*cfg['samples'])
    for item in result:
        for op in ['sign','verify']:item[op]['ecdsa_ratio']=item[op]['cpu_us']/result[0][op]['cpu_us']
    return result

def verify(path):
    data=read(path)
    assert data['summary']==aggregate(data)
    assert data['corpus']['sha256']==sha(corpus())
    for name,source_ in data['source_snapshot'].items():
        assert hashlib.sha256(source_['content'].encode()).hexdigest()==source_['sha256'],name
        assert data['build']['identity']['files'][name]==source_['sha256'],name
    assert data['environment_before']['profile']==data['profile']==data['build']['identity']['profile']
    published_log=Path(path).parent/'checks.txt'
    if published_log.exists():assert sha(published_log)==data['build']['checks_sha256'],'checks log hash'
    print('PASS: six algorithms, counts, message indices, sizes, round statistics, ratios and source snapshots')
    return data

NOTES=[
    'BSC / Ethereum EOA 签名基准；本表测已知公钥验签。',
    '本地 v3；TRON TIP-899 / Nile 代码采用此参数。',
    '本地 v4；TRON TIP-899 的另一选项。',
    '本地 v5；Sui 原生账户选型。',
    '本地 v1 / v2；Sui Move 金库选型。',
    '本地 v6；与 128s 对照签名大小和耗时。',
]
def render(data):
    foundations = {
        'ECDSA-secp256k1': ('椭圆曲线离散对数问题', False),
        'Falcon-512': ('NTRU 格上的短向量问题', True),
        'ML-DSA-44': ('模格问题（Module-LWE / Module-SIS）', True),
        'ML-DSA-65': ('模格问题（Module-LWE / Module-SIS）', True),
        'SLH-DSA-SHA2-128s': ('哈希函数（SHA-2）与 Merkle 树', True),
        'SLH-DSA-SHA2-128f': ('哈希函数（SHA-2）与 Merkle 树', True),
    }
    def cell(item,op,baseline):
        label='baseline' if baseline else f'{item[op]["ecdsa_ratio"]:,.2f} 倍 baseline'
        return f'{item[op]["cpu_us"]:,.2f} us, {label}'
    rows='';details=''
    for i,item in enumerate(data['summary']):
        sig=f'{item["signature_min_bytes"]:,}' if item['signature_min_bytes']==item['signature_max_bytes'] else f'{item["signature_min_bytes"]:,}–{item["signature_max_bytes"]:,}'
        math_basis, quantum_resistant = foundations[item['algorithm']]
        algorithm_cell = (f'<td class="algorithm">{html.escape(item["algorithm"])}'
                          f'<small>基础：{html.escape(math_basis)}</small>'
                          f'<small>抗量子：{"是" if quantum_resistant else "否"}</small></td>')
        rows+=f'<tr>{algorithm_cell}<td>{item["private_key_bytes"]:,} bytes</td><td>{item["public_key_bytes"]:,} bytes</td><td>{sig} bytes</td><td>{cell(item,"sign",i==0)}</td><td>{cell(item,"verify",i==0)}</td><td>{NOTES[i]}</td></tr>'
        for op in ['sign','verify']:
            vals=item[op]
            details+=f'<tr><td>{item["algorithm"]}</td><td>{"签名" if op=="sign" else "验签"}</td><td>'+ ' / '.join(f'{v:,.2f} us' for v in vals['round_cpu_us'])+f'</td><td>{vals["wall_us"]:,.2f} us</td><td>{vals["process_us"]:,.2f} us</td></tr>'
    env=data['environment_before'];cfg=data['config'];profile=data['profile']
    message_counts={op:len({(r['message_start_index']+i)%data['corpus']['messages']
        for r in data['rows'] if r['kind']=='timing' and r['algorithm']==NAMES[0] and r['operation']==op
        for i in range(r['iterations'] if op=='sign' else 32)}) for op in ['sign','verify']}
    falcon_rows=[r for r in data['rows'] if r['kind']=='timing' and r['algorithm']=='Falcon-512' and r['operation']=='sign']
    observed_falcon=f"{min(r['signature_observed_min_bytes'] for r in falcon_rows)}–{max(r['signature_observed_max_bytes'] for r in falcon_rows)}"

    source_links=''.join(f'<li><a href="{spec["repository"]}/tree/{spec["commit"]}">{html.escape(spec["version"])} · {spec["commit"][:12]}</a></li>' for spec in data['build']['identity']['sources'].values())
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Go 签名算法对比 · {profile}</title>
<style>body{{font:15px/1.75 system-ui;max-width:1440px;margin:32px auto;padding:0 24px;color:#243447}}h1,h2{{line-height:1.4}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}td,th{{padding:10px;border:1px solid #dce3eb;text-align:right;vertical-align:top}}td:first-child,th:first-child,td:last-child{{text-align:left}}th{{background:#edf3f8}}.scroll{{overflow:auto}}a{{color:#146caa}}.note{{padding:16px;background:#edf4fa;border-left:4px solid #3477a4}}details{{margin:24px 0}}pre{{white-space:pre-wrap}}small{{color:#526477}}.algorithm{{min-width:180px}}.algorithm small{{display:block;margin-top:4px;font-size:12px;line-height:1.5;overflow-wrap:anywhere}}</style>
<h1>签名算法对比 · Go 接口</h1><p>{html.escape(env['cpu'])} · {profile} · {html.escape(data['started_at'].split('T')[0])}</p>
<div class="note">六项算法共用 Go 接口，每次 Sign / Verify 调用一次 C 后端，计入 Go 和 CGO 开销。密钥生成和可复用对象创建不计时，ECDSA 复用已解析的公钥对象。这是本机热密钥测试，完整交易 gas 见<a href="../chain-bench/report.html">链路报告</a>。</div>
<h2>测试结果</h2><p>耗时越小越快；签名和验签分别以本次 ECDSA 为 baseline。每轮逐批平均后取中位数，再取各轮中位数。</p>
<div class="scroll"><table><thead><tr><th rowspan="2">算法 / 参数</th><th colspan="3" style="text-align:center">存储</th><th colspan="2" style="text-align:center">计算</th><th rowspan="2">备注</th></tr><tr><th>私钥编码</th><th>公钥</th><th>签名</th><th>签名 CPU</th><th>验签 CPU</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>“抗量子”指在相应参数与安全假设下，抵抗目前已知的量子攻击，不是无条件安全保证。</p>
<p>“存储”列是本实验使用的编码大小，不是对象内存或链上存储 gas。ML 私钥列为展开编码，不能与 32 bytes 种子备份直接比较。SLH 的 64 bytes 私钥编码包含 32 bytes 公钥；ECDSA 的 64 bytes 签名不含恢复位。</p>
<p>Falcon 公钥 / 私钥省略固定的 1-byte 头，完整编码为 897 / 1,281 bytes。签名保留头部；617–667 bytes 是本适配器接受范围，本轮正式签名实际为 {observed_falcon} bytes。它不是 Falcon 的通用长度上限，也不是 TVM 的固定 666-byte 槽。</p>
<h2>测试口径</h2><ul>
<li>同一 Go 可执行文件、单线程顺序测试；GOMAXPROCS(1)、LockOSThread，计时循环预分配缓冲区并关闭 Go GC。没有在 C 内部批量操作以摊薄 CGO。</li>
<li>使用同一 4,096 条、每条 32 bytes 的公开语料池。每项 {cfg['rounds']} 轮 × {cfg['samples']} 批，每批签名 {cfg['sign_count']} 次、验签 {cfg['verify_count']} 次；每轮换钥并轮转算法顺序。本轮签名实际覆盖 {message_counts['sign']:,} 条消息；验签每轮循环最后 32 对消息和签名，共覆盖 {message_counts['verify']:,} 条消息。各算法使用相同索引。</li>
<li>ECDSA 签署 32 bytes 摘要；其余签同一 32 bytes 原始消息。ML / SLH 是 Pure、空上下文，内部哈希计入。安全随机数、拒绝采样、签名编码与验签均不省略。ECDSA 是确定性 nonce，ML / Falcon 读取系统新熵，SLH 使用 OpenSSL 安全 DRBG；随机数需求与实现不同，不宣称等量随机计算。</li>
<li>密钥生成和可复用对象创建不计时；SLH 每次调用的 EVP reset / Init 仍计时。ECDSA 公钥对象和 ML 展开密钥可复用。Falcon 每次动态签名都重新解码密钥，不缓存展开的 FFT 密钥。不同算法的初始化成本并未全部放入主表。</li>
<li>共同 C 编译参数：<code>{html.escape(' '.join(data['build']['identity']['flags']))}</code>。各库保留适合 ARM 的汇编 / NEON，编译选项相同不代表内部优化程度完全相同。</li>
<li>ECDSA 用 libsecp256k1；ML 两参数用同一 mldsa-native；Falcon 用原版 Falcon NEON；SLH 用 OpenSSL C / ARM SHA-256。外层接口相同，底层实现与优化程度不同。</li>
</ul><p>线程 CPU 由相同 CGO 时钟接口读取，锁定同一 OS 线程；另存墙钟及进程 CPU。macOS 不保证固定性能核和频率，环境、电源与温度快照保存在原始数据中。</p>
<h2>验证与判断</h2><p>每轮 32 个正例及消息篡改、签名篡改、截短、错钥四类负例；所有计时调用检查成功，签名每批末重新验签。ML 两参数与 OpenSSL 互验；Falcon 通过公开 Falcon KAT 及 NEON 上游测试。每项正式签名 {cfg['rounds']*cfg['samples']*cfg['sign_count']:,} 次、验签 {cfg['rounds']*cfg['samples']*cfg['verify_count']:,} 次。原始数据复算同时检查语料索引、次数、尺寸、顺序和 ECDSA 倍数。</p>
<p>本轮 128f 比 128s 签名快，但签名更大、验签更慢；两者均为 NIST 类别 1。ML-DSA-44 / 65 分属类别 2 / 3，较大参数带来更多计算和数据。Falcon 签名小、验签快，但签名采样实现较复杂。耗时排名不等于安全等级，也不能直接换算交易 gas。</p>
<h2>与链路报告怎么对照</h2>
<p><a href="../chain-bench/report.html">链路报告</a>测实际 BSC 预编译和完整交易。两页用途不同，CPU 数字不能直接横比：</p>
<div class="scroll"><table><tr><th>项目</th><th>本报告</th><th>链路报告</th></tr><tr><td>SLH 后端</td><td>OpenSSL C / ARM SHA-256</td><td>Trail of Bits Go</td></tr><tr><td>CPU 主指标</td><td>线程 CPU；Go GC 关闭</td><td>进程 CPU；Go GC 开启</td></tr><tr><td>ECDSA 操作</td><td>已知公钥验签</td><td>ECRECOVER，恢复公钥并计算地址</td></tr><tr><td>计时范围</td><td>当前 Sign / Verify 接口调用</td><td>CPU 表测 Run；gas 表测完整外层交易</td></tr></table></div>
<p>因此，SLH 在两页的耗时差异不能归结为“接入预编译后变慢”。本页的相对 ECDSA 倍数也不用于链上的 ECRECOVER 定价。</p>
<details><summary>五轮统计、墙钟与进程 CPU</summary><div class="scroll"><table><tr><th>算法</th><th>操作</th><th>每轮线程 CPU 中位数</th><th>墙钟主值</th><th>进程 CPU 主值</th></tr>{details}</table></div><p>Go 后台线程的 CPU 不在主线程 CPU 内；进程 CPU 是辅助观察，不直接替换主表。无锁频、无硬核亲和，少量波动不应解释为普适优势。</p></details>
<details><summary>算法定义、采用场景与来源</summary><p>ML / SLH 分别依据 <a href="https://doi.org/10.6028/NIST.FIPS.204">FIPS 204</a> / <a href="https://doi.org/10.6028/NIST.FIPS.205">FIPS 205</a>；本表采用原版 Falcon-512；<a href="https://csrc.nist.gov/projects/post-quantum-cryptography/post-quantum-cryptography-standardization/selected-algorithms">NIST 页面</a>在本次核查时仍将其对应 FIPS 列为待发布，不能等同 FN-DSA 定稿实现。采用场景依据下列提案与固定提交：TIP-899 仍是草案，不表示 TRON 主网已启用；所引 Sui 提交仅对 Devnet 启用 ML65，Move 金库选型不证明主网部署或使用量。</p><p><a href="https://github.com/tronprotocol/tips/issues/899">TRON TIP-899</a> · <a href="https://github.com/tron-nile-testnet/nile-testnet/tree/bc7752b638d9aa4da413c78645dcff1a1aa8e40a/crypto/src/main/java/org/tron/common/crypto/pqc">Nile 固定实现</a> · <a href="https://www.sui.io/blog/suis-post-quantum-signature-schemes">Sui 选型</a> · <a href="https://github.com/MystenLabs/sui/blob/16d2dc1e05edcf600320a82e17b9392edb0983a5/crates/sui-protocol-config/src/lib.rs#L4901">Sui Devnet 启用条件</a> · <a href="https://github.com/MystenLabs/mysten-mldsa-native-rs/blob/e6451eca50f8a2d7b7d455fa44530357d0b626a3/PROVENANCE.md">Sui 后端来源</a> · <a href="https://pq-code-package.github.io/mldsa-native/dev/bench/">ML 官方性能</a> · <a href="https://csrc.nist.gov/csrc/media/Events/2022/fourth-pqc-standardization-conference/documents/papers/fast-falcon-signature-generation-and-verification-pqc2022.pdf">Falcon ARM 论文</a> · <a href="https://github.com/manel1874/slh-dsa-bench">SLH 公开评测</a>。</p><ul>{source_links}</ul></details>
<h2>环境与复现</h2><p>本次仅测 macOS arm64。Linux x86_64 尚未接入和验证 Falcon 后端，脚本会拒绝运行该配置。机器、编译参数、依赖和测量时源码快照保存在原始记录中；文档修订不改写历史样本或源码摘要。</p>
<p>入口：<code>python3 tools/crypto-bench/run.py --profile macos-arm64</code>；复核：<code>python3 tools/crypto-bench/run.py --verify</code>。<a href="{profile}/results.json">原始样本、统计、环境、构建命令与源码快照</a> · <a href="{profile}/checks.txt">验证日志</a>。两份报告的交叉复核：<code>python3 tools/chain-bench/audit.py</code>。</p></html>'''
    (DOCS/'comparison.html').write_text(page)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',choices=['auto',*PROFILES],default='auto')
    p.add_argument('--rounds',type=int,default=5);p.add_argument('--samples',type=int,default=7)
    p.add_argument('--sign-count',type=int,default=32);p.add_argument('--verify-count',type=int,default=256)
    p.add_argument('--build-only',action='store_true');p.add_argument('--smoke',action='store_true');p.add_argument('--verify',action='store_true');p.add_argument('--render',action='store_true')
    args=p.parse_args()
    if args.verify or args.render:
        profiles=[args.profile] if args.profile!='auto' else [d.name for d in DOCS.iterdir() if (d/'results.json').exists()]
        for profile in profiles:
            data=verify(DOCS/profile/'results.json')
            if args.render:render(data)
        return
    profile=detect_profile(args.profile)
    if not 1<=args.rounds<=99 or not 1<=args.samples<=100:p.error('invalid rounds/samples')
    if any(v<32 or v>65536 or v%32 for v in [args.sign_count,args.verify_count]):p.error('counts must be multiples of 32, 32..65536')
    no_devnet();metadata=build(profile)
    if args.build_only:return
    config={'rounds':1 if args.smoke else args.rounds,'samples':1 if args.smoke else args.samples,'sign_count':args.sign_count,'verify_count':args.verify_count}
    print('Build finished; allow 20 seconds before measurement.',flush=True);time.sleep(20)
    path=corpus();before=environment(profile);rows=[];started=dt.datetime.now().astimezone()
    run_folder=CACHE/'runs'/started.strftime('%Y%m%d-%H%M%S');run_folder.mkdir(parents=True,exist_ok=False)
    with (run_folder/'raw.jsonl').open('w') as raw:
        for round_ in range(config['rounds']):
            no_devnet()
            for order in range(len(NAMES)):
                index=(round_+order)%len(NAMES);print(f'Round {round_+1}/{config["rounds"]}: {NAMES[index]}',flush=True)
                cmd=[str(CACHE/profile/'bench'),str(round_),str(config['samples']),str(config['sign_count']),str(config['verify_count']),str(path),str(index)]
                env={k:v for k,v in os.environ.items() if not k.startswith(('OPENSSL_','DYLD_','GODEBUG','GOMEMLIMIT','GOGC'))};env['OPENSSL_CONF']='/dev/null'
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,text=True,env=env)
                for line in proc.stdout:
                    rows.append(json.loads(line));raw.write(line);raw.flush()
                if proc.wait():raise RuntimeError('Benchmark failed; partial data retained in '+str(run_folder))
    data={'schema':3,'profile':profile,'started_at':started.isoformat(),'finished_at':dt.datetime.now().astimezone().isoformat(),
          'config':config,'corpus':{'version':1,'messages':4096,'message_bytes':32,'sha256':sha(path)},
          'environment_before':before,'environment_after':environment(profile),'build':metadata,'rows':rows,
          'source_snapshot':{name:{'sha256':sha(HERE/name),'content':(HERE/name).read_text()} for name in SOURCE_FILES},
          'repository_head':output(['git','rev-parse','HEAD'])}
    data['summary']=aggregate(data);save(run_folder/'results.json',data);verify(run_folder/'results.json')
    if not args.smoke:
        dest=DOCS/profile;dest.mkdir(parents=True,exist_ok=True);shutil.copy2(run_folder/'results.json',dest/'results.json');shutil.copy2(CACHE/profile/'checks.txt',dest/'checks.txt');render(data)
        print(DOCS/'comparison.html')
    for row in data['summary']:print(row['algorithm'],f'sign={row["sign"]["cpu_us"]:.2f} us, verify={row["verify"]["cpu_us"]:.2f} us')

if __name__=='__main__':main()
