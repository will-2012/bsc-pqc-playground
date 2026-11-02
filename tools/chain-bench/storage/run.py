#!/usr/bin/env python3
"""Local lifecycle adapter. Load and measure commands also work on physical hosts."""
import argparse, datetime, fcntl, gzip, hashlib, json, os, platform, shutil, signal, subprocess, sys, time, urllib.request
from pathlib import Path
from measure import measure, digest, allocated
from report_privacy import public_data
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
BOX=ROOT/'one-box/.runtime'
DOCS=ROOT/'docs/chain-bench/storage'

def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    if p.resolve().is_relative_to(DOCS):v=public_data(v)
    tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n');tmp.replace(p)

def rpc(url,method,params=[]):
    req=urllib.request.Request(url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=60) as f:r=json.load(f)
    if 'error' in r:raise RuntimeError(r['error'])
    return r['result']

def call(args):
    subprocess.run(list(map(str,args)),cwd=ROOT,check=True)

def box(name,*args):call([ROOT/'tools/one-box','--name',name,*args])

def guard(path,minimum=30):
    free=shutil.disk_usage(path).free
    if free<minimum*2**30:raise RuntimeError(f'Free disk {free/2**30:.1f} GiB below {minimum} GiB; stop before next stage')
    return free

def snapshot(name,work,out,count):
    # Stop producers first, then wait until all three nodes have exactly the same head.
    sys.path.insert(0,str(ROOT/'one-box/scripts'));import devnet
    env=BOX/name; cfg=json.loads((work/'load.json').read_text())
    # Let the final account writes pass the 128 in-memory diff layers.
    last=int(rpc(cfg['rpc'],'eth_blockNumber'),16)
    deadline=time.time()+180
    while int(rpc(cfg['rpc'],'eth_blockNumber'),16)<last+160:
        if time.time()>deadline:raise RuntimeError('Settlement blocks timed out')
        guard(env);time.sleep(2)
    for i in range(3):devnet.attach(env,i,'miner.stop()')
    heads=None
    for _ in range(60):
        heads=[rpc(n['rpc'],'eth_getBlockByNumber',['latest',False]) for n in cfg['nodes']]
        if len({h['hash'] for h in heads})==1:break
        time.sleep(1)
    else:raise RuntimeError('Nodes did not converge before snapshot')
    assert len({h['stateRoot'] for h in heads})==1
    if (work/'progress.json').exists():
        progress=json.loads((work/'progress.json').read_text())
        canonical=rpc(cfg['rpc'],'eth_getBlockByNumber',[hex(progress['lastBlock']),False])
        if canonical['hash']!=progress['lastHash']:raise RuntimeError('Last batch was reorged; cannot publish measurements')
    box(name,'down')
    stage=out/str(count);stage.mkdir(parents=True,exist_ok=True)
    save(stage/'head.json',{k:heads[0][k] for k in ['number','hash','stateRoot','timestamp','gasLimit']})
    for i in range(3):
        result=measure(env/'bin/geth',env/f'node{i}',stage,f'node{i}',count)
        if count:
            base=json.loads((out/'0'/f'node{i}.json').read_text())
            accounts=lambda x:next(r['items'] for r in x['inspect'] if r['category']=='Account snapshot')
            if accounts(result)-accounts(base)!=count:
                raise RuntimeError(f'node{i}: snapshot account delta does not equal {count}; do not publish this stage')
    save(stage/'run.json',{'count':count,'progress':json.loads((work/'progress.json').read_text()) if (work/'progress.json').exists() else {},
         'nodeLogsBytes':allocated(env/'logs'),'walletAndLoadBytes':allocated(work),'freeBytes':shutil.disk_usage(env).free})

def render(report):
    import html
    data=json.loads(report.read_text());rows=[];source_notes=[];measured={}
    selection=report.parent/'report-sources.json'
    overrides=json.loads(selection.read_text()) if selection.exists() else {}
    for algo,g in data['groups'].items():
        source=report.parent;source_algo=algo;label=algo
        if algo in overrides:
            choice=overrides[algo];source=report.parent.parent/choice['run'];source_algo=choice['algorithm']
            other=json.loads((source/'manifest.json').read_text())
            for key in ['binarySha256','config','historyPolicy','platform']:
                if other[key]!=data[key]:raise ValueError('Incompatible report source: '+key)
            g=other['groups'][source_algo];label=choice['label']
            if g.get('completed')!=data['groups'][algo].get('completed'):raise ValueError('Replacement stages differ')
            source_notes.append('<a href="../'+html.escape(choice['run'],quote=True)+'/manifest.json">'+html.escape(label)+' 数据来源</a>')
        for count in g.get('completed',[]):
            stage=source/source_algo/str(count)
            categories=['Account snapshot','Storage snapshot','Path trie account nodes','Path trie storage nodes','Contract codes']
            totals=[0.0]*len(categories);item_totals=[0]*len(categories);physical=0
            for i in range(3):
                sample=json.loads((stage/f'node{i}.json').read_text());base=json.loads((source/source_algo/'0'/f'node{i}.json').read_text())
                def sizes(x):return {r['category']:r['approxBytes'] for r in x['inspect'] if r['store']=='Key-Value store'}
                a,b=sizes(sample),sizes(base)
                for j,key in enumerate(categories):
                    totals[j]+=a[key]-b[key]
                    items=lambda x:next(r['items'] for r in x['inspect'] if r['store']=='Key-Value store' and r['category']==key)
                    item_totals[j]+=items(sample)-items(base)
                physical+=sample['physicalBytes']['chaindata']-base['physicalBytes']['chaindata']
            measured[source_algo,count]={'bytes':dict(zip(categories,[v/3 for v in totals])),'items':dict(zip(categories,[v/3 for v in item_totals]))}
            vals=[label,f'{count:,}',*(f'{v/3/2**20:,.2f}' for v in totals),f'{physical/3/2**20:,.2f}',f'{physical/3/count:,.1f}']
            rows.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in vals)+'</tr>')
    headers=['方案','账户数','账户 snapshot MiB','Storage snapshot MiB','账户 Trie MiB','Storage Trie MiB','代码 MiB','落盘增量 MiB','落盘 bytes/账户']
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>百万账户存储实验</title><style>body{max-width:1280px;margin:40px auto;padding:0 24px;font:16px/1.7 system-ui;color:#20334a}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d6e2ee;padding:10px;text-align:right}th{background:#edf4fa}section{overflow:auto}h2{margin-top:36px}h3{margin-top:24px}td:first-child,th:first-child{text-align:left}details{margin:20px 0}summary{cursor:pointer;color:#356080}.status{background:#f0f7f3;padding:14px}</style><h1>账户规模与节点存储</h1>'''
    page+=f'<p class="status">状态：{html.escape(data["status"])}。只展示已完成停机测量的数据；空缺不是零。</p>'
    page+='<p>三节点 fullnode、path 状态、Pebble；各方案独立新链，阶梯累计到 1 万、10 万、100 万。ECDSA 为入账 1 wei 的普通 EOA；PQ 为现有 Factory 部署的独立合约，同样余额 1 wei。PQ 小样本另验 UserOperation，在零账户基线前完成。此实验不测百万次 UserOperation 吞吐。</p>'
    page+='<p>统一使用零 path 写缓冲，每阶段额外出 160 块后停机，核对账户 snapshot 增量与目标数量相等。此配置用于存储分类，不代表生产吞吐。表内为三个节点分别扣除基线后的平均增量，表示单节点占用，不是三节点合计；原始分节点数据保留。inspect 的 KV 分类是经过显示舍入的逻辑 key+value 大小；落盘为 du 实际分配量，两者不能相加。落盘值统一经过离线 compact，压实前大小也保留。未裁剪区块或历史状态；历史和索引保存在原始 inspect 中。三节点共同使用本机 CPU 和磁盘。</p>'
    if source_notes:
        page+='<p>SLH 使用 v7 公钥存 storage、代码可复用模型；旧 immutable 原始数据保留。'+ ' · '.join(source_notes)+'</p>'
    page+='<section><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></section>'
    from theory import render_theory
    page+=render_theory(measured)
    page+='<p><a href="manifest.json">运行记录</a> · <a href="../../../../tools/chain-bench/storage/README.md">实验口径与复现</a></p></html>'
    (report.parent/'report.html').write_text(page)
    if data.get('referenceRun'):
        from compare import generate
        generate(DOCS/data['referenceRun'], report.parent, report.parent/'comparison.html')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',required=True,help='Unique run identifier, letters/digits/hyphens')
    p.add_argument('--steps',default='10000,100000,1000000');p.add_argument('--algorithms',default='ecdsa,ml,slh')
    p.add_argument('--reference-run',help='Existing compatible run for automatic ECDSA/ML/SLH comparison')
    p.add_argument('--keep',action='store_true',help='Keep completed chain (default removes only the owned experiment environment)')
    a=p.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('Stop requested')))
    import re
    if not re.fullmatch('[a-z0-9-]{1,22}',a.run):p.error('Invalid run identifier')
    steps=list(map(int,a.steps.split(',')));assert steps==sorted(set(steps)) and steps[0]>0
    algorithms=a.algorithms.split(',');assert all(x in ['ecdsa','ml','slh','slh-storage'] for x in algorithms)
    workroot=BOX/'storage-bench'/a.run;workroot.mkdir(parents=True,exist_ok=True);os.chmod(workroot,0o700)
    lock=(workroot/'run.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    out=DOCS/a.run;out.mkdir(parents=True,exist_ok=True);record=out/'manifest.json'
    config=json.loads((ROOT/'one-box/config.json').read_text());config.update(rpc_port=21545,p2p_port=34311,cache_mb=1024,test_balance_bnb=1000000,pq_precompile=6,trie_dirty_cache_percent=0)
    configpath=workroot/'config.json';save(configpath,config)
    if record.exists():
        data=json.loads(record.read_text());assert data['steps']==steps and data['algorithms']==algorithms
        data['status']='running'
    else:
        data={'schema':1,'status':'running','steps':steps,'algorithms':algorithms,'groups':{},'platform':platform.platform(),
              'startedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'config':config,'historyPolicy':'full; no pruning',
              'gitCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'sourceHashes':{str(f.relative_to(ROOT)):digest(f) for f in HERE.iterdir() if f.is_file() and f.suffix in ['.py','.mjs','.c','.sol','.toml']},
              'binarySha256':digest(BOX/'build/v6/geth'),'build':json.loads((BOX/'storage-bench/build.json').read_text())}
    if a.reference_run:
        if not re.fullmatch('[a-z0-9-]{1,22}',a.reference_run):p.error('Invalid reference run')
        if data.get('referenceRun',a.reference_run)!=a.reference_run:raise ValueError('Reference run changed')
        data['referenceRun']=a.reference_run
        save(record,data)
        render(record)
    active=None
    try:
      for algo in algorithms:
        g=data['groups'].setdefault(algo,{'completed':[]})
        if g.get('finished'):continue
        guard(workroot,40);name=f'storage-{hashlib.sha256(a.run.encode()).hexdigest()[:6]}-{"slhs" if algo == "slh-storage" else algo}';active=name;env=BOX/name;work=workroot/algo;work.mkdir(exist_ok=True)
        g['environment']=name;save(record,data);render(record)
        marker=work/'owns-environment'
        if not marker.exists():
            if env.exists():raise RuntimeError('Refusing to reuse an unowned environment')
            marker.write_text(str(env));box(name,'init','--config',configpath,'--v6')
        if not (work/'load.json').exists():
            box(name,'bundler','up','--port','15387')
            call(['node',HERE/'setup.mjs',name,algo,work])
        private=json.loads((work/'load.json').read_text())
        g['loadConfig']={k:v for k,v in private.items() if k not in ['keystore','passwordFile']}
        g['artifactHashes']={key:digest(private[key]) for key in ['batchArtifact','accountArtifact'] if key in private}
        save(record,data)
        if not (out/algo/'0/node2.json').exists():snapshot(name,work,out/algo,0)
        for count in steps:
            if count in g['completed']:continue
            free=guard(workroot)
            if g['completed']:
                previous=max(g['completed']);delta=0
                for i in range(3):
                    old=json.loads((out/algo/str(previous)/f'node{i}.json').read_text())
                    base=json.loads((out/algo/'0'/f'node{i}.json').read_text())
                    delta+=max(0,old['physicalBytes']['chaindata']-base['physicalBytes']['chaindata'])
                projected=delta*(count/previous-1)*1.5
                g['nextStageBudget']={'count':count,'freeBytes':free,'projectedAdditionalBytes':projected,'reserveBytes':30*2**30}
                save(record,data)
                if projected+30*2**30>free:raise RuntimeError('Projected next stage exceeds disk budget; preserve measured data')
            box(name,'up')
            call(['node',HERE/'load.mjs',work/'load.json',count])
            snapshot(name,work,out/algo,count)
            g['completed'].append(count);save(record,data);render(record)
        # Archive only public evidence; secrets stay in ignored runtime.
        shutil.copy2(work/'functional-samples.json',out/algo/'functional-samples.json')
        with (work/'batches.jsonl').open('rb') as src, gzip.open(out/algo/'batches.jsonl.gz','wb') as dst:
            shutil.copyfileobj(src,dst)
        g['finished']=True;g['freeBeforeCleanup']=shutil.disk_usage(workroot).free
        if not a.keep:
            assert marker.read_text()==str(env) and env.parent==BOX and env.name.startswith('storage-')
            box(name,'down');shutil.rmtree(env);g['chainRemoved']=True
        g['freeAfterCleanup']=shutil.disk_usage(workroot).free;save(record,data);render(record);active=None
      data['status']='complete';save(record,data);render(record)
    except BaseException as e:
      data['status']='paused: '+str(e);save(record,data);render(record)
      if active and (BOX/active/'environment.json').exists():
        try:box(active,'down')
        except Exception:pass
      raise

if __name__=='__main__':main()
