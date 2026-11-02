#!/usr/bin/env python3
"""Compare completed storage stages; never estimate missing measurements."""
import argparse, html, json, math
from pathlib import Path

CATEGORIES = {
    'accounts': ['Account snapshot'],
    'storage': ['Storage snapshot'],
    'state': ['Account snapshot', 'Storage snapshot'],
    'code': ['Contract codes'],
    'state_code': ['Account snapshot', 'Storage snapshot', 'Contract codes'],
    'account_trie': ['Path trie account nodes'],
    'storage_trie': ['Path trie storage nodes'],
    'trie': ['Path trie account nodes', 'Path trie storage nodes'],
}
LABELS = {'accounts':'账户快照','storage':'Storage 快照','state':'State 快照合计','code':'合约代码',
          'state_code':'State 快照＋代码','account_trie':'账户 Trie','storage_trie':'Storage Trie','trie':'Trie 合计','disk':'压实后磁盘'}
NAMES = {'ecdsa':'ECDSA EOA','ml':'ML-DSA-44','slh':'SLH immutable（v2）','slh-storage':'SLH storage（v7）'}
def read(p): return json.loads(Path(p).read_text())
def values(sample):
    rows={r['category']:r for r in sample['inspect'] if r['store']=='Key-Value store'}
    return {k:sum(rows[c]['approxBytes'] for c in cats) for k,cats in CATEGORIES.items()} | {'disk':sample['physicalBytes']['chaindata']}

def generate(reference, candidate, output):
    reference,candidate,output=map(Path,(reference,candidate,output))
    old,new=read(reference/'manifest.json'),read(candidate/'manifest.json')
    # Contract/profile and random keys differ deliberately; node/storage settings must match.
    for key in ['binarySha256','config','historyPolicy','platform']:
        if old[key]!=new[key]:raise ValueError('Incompatible comparison field: '+key)
    records=[]
    for folder,manifest,algorithms in [(reference,old,['ecdsa','ml','slh']),(candidate,new,['slh-storage'])]:
      for algo in algorithms:
        group=manifest['groups'].get(algo,{})
        for n in group.get('completed',[]):
          for node in range(3):
            base=read(folder/algo/'0'/f'node{node}.json');sample=read(folder/algo/str(n)/f'node{node}.json')
            count=lambda x:next(r['items'] for r in x['inspect'] if r['category']=='Account snapshot')
            if count(sample)-count(base)!=n:raise ValueError('Account count mismatch')
            a,b=values(sample),values(base)
            records.append({'algorithm':algo,'accounts':n,'node':node,'deltaBytes':{k:a[k]-b[k] for k in a}})
    summary=[]
    for algo in NAMES:
      for n in sorted({r['accounts'] for r in records if r['algorithm']==algo}):
        rows=[r for r in records if r['algorithm']==algo and r['accounts']==n]
        baseline=[r for r in records if r['algorithm']=='ecdsa' and r['accounts']==n]
        if len(rows)!=3 or len(baseline)!=3:raise ValueError('Missing node or ECDSA stage')
        metrics={}
        for k in LABELS:
            total=sum(r['deltaBytes'][k] for r in rows);base=sum(r['deltaBytes'][k] for r in baseline)
            avg=total/3;per=avg/n;ratio=total/base if base else None
            if ratio is not None and not math.isclose(ratio,per/(base/3/n),rel_tol=1e-12):raise ValueError('Amplification mismatch')
            metrics[k]={'threeNodesBytes':total,'meanNodeBytes':avg,'meanBytesPerAccount':per,'ecdsaRatio':ratio}
        summary.append({'algorithm':algo,'accounts':n,'metrics':metrics})
    payload={'referenceRun':reference.name,'candidateRun':candidate.name,'candidateStatus':new['status'],
             'definition':'Baseline-subtracted; snapshot state excludes code and trie; logical sizes rounded by inspect; disk includes history.',
             'settingsMatch':True,'records':records,'summary':summary}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.with_suffix('.json').write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    esc=html.escape
    parts=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>账户存储对照</title><style>body{max-width:1200px;margin:36px auto;padding:0 24px;font:16px/1.7 system-ui;color:#20334a}section{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}th,td{border:1px solid #d6e2ee;padding:9px;text-align:right}th{background:#edf4fa}td:first-child{text-align:left}.new{background:#eef8f2}.note{background:#f0f5fa;padding:14px}h2{margin-top:32px}</style><h1>账户存储对照：ECDSA、ML 与 SLH</h1>''']
    parts.append('<p class="note">v7 状态：'+esc(new['status'])+'。仅展示已完成阶段；未完成项不外推。</p>')
    parts.append('<p>三节点、独立新链、每账户 1 wei，PQ 由 Factory 批量创建，少量 UserOperation 在基线前验证。复用原实验 ECDSA / ML / SLH immutable 实测数据，v7 单独重跑。客户端、节点配置与历史策略已自动核对一致；随机密钥、部署地址及执行时间不同。</p>')
    parts.append('<p>State 快照＝账户 snapshot＋storage snapshot；代码另列。Trie＝账户 Trie＋storage Trie。逻辑大小是 inspect 的 key/value 统计，有显示舍入；实际磁盘使用压实后 chaindata，含交易历史和索引。各表均已扣除该组基线，不能把逻辑大小与磁盘大小相加。</p>')
    for n in sorted({r['accounts'] for r in summary}):
        parts.append(f'<h2>{n:,} 个账户</h2><section><table><tr><th>方案</th><th>指标</th><th>每节点平均总量 MiB</th><th>每账户 bytes</th><th>相对 ECDSA</th><th>三节点合计 MiB</th></tr>')
        for row in [r for r in summary if r['accounts']==n]:
          for k in LABELS:
            m=row['metrics'][k];ratio='—' if m['ecdsaRatio'] is None else f'{m["ecdsaRatio"]:,.2f} 倍'
            parts.append(('<tr class="new">' if row['algorithm']=='slh-storage' else '<tr>')+f'<td>{NAMES[row["algorithm"]]}</td><td>{LABELS[k]}</td><td>{m["meanNodeBytes"]/2**20:,.2f}</td><td>{m["meanBytesPerAccount"]:,.2f}</td><td>{ratio}</td><td>{m["threeNodesBytes"]/2**20:,.2f}</td></tr>')
        parts.append('</table></section>')
    parts.append('<h2>如何核查</h2><p>同样账户数下，总增量倍数应等于平均每账户增量倍数，生成脚本已校验。快照账户数增量必须准确等于目标数量。JSON 保留每个节点的分项，不以一个节点乘三替代实测。ECDSA 代码增量为零，代码项倍数标为“—”。</p><p>ML 公钥占 42 个 storage 槽；旧 SLH 公钥嵌入代码，不同公钥产生不同代码；v7 公钥占一个槽，同一工厂下代码可复用。系统状态也会随实验时长变化，因此小额 storage 差异不能全部归到用户账户。合约部署、状态与历史成本共同影响磁盘结果。</p>')
    parts.append('<p><a href="comparison.json">分节点数据与计算结果</a> · <a href="report.html">v7 原始阶段报告</a> · <a href="../'+esc(reference.name)+'/report.html">原三组阶段报告</a></p></html>')
    output.write_text(''.join(parts))
    return payload

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reference',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();generate(a.reference,a.candidate,a.output)
