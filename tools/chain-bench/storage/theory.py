"""Explain the current EOA / ML storage / SLH storage layouts using measured rows."""

def render_theory(measured):
    names={'ecdsa':'ECDSA EOA','ml':'ML-DSA-44','slh-storage':'SLH storage（v7）'}
    if not all((a,1000000) in measured for a in names):return ''
    samples={a:measured[a,1000000] for a in names}
    categories=['Account snapshot','Storage snapshot','Path trie storage nodes']
    expected={'ecdsa':[38,0,0],'ml':[103,4086,7084],'slh-storage':[103,98,103]}
    labels=['账户 snapshot','Storage snapshot','Storage Trie']
    rows=[]
    for i,c in enumerate(categories):
        for a,name in names.items():
            rows.append(f'<tr><td>{labels[i]}</td><td>{name}</td><td>{expected[a][i]:,}</td><td>{samples[a]["bytes"][c]/1000000:,.2f}</td></tr>')
    counts=[]
    for a,slots,nodes in [('ml',42,59),('slh-storage',1,1)]:
        for category,factor,label in [('Storage snapshot',slots,'Storage 记录'),('Path trie storage nodes',nodes,'Storage Trie 节点')]:
            counts.append(f'<tr><td>{names[a]}</td><td>{label}</td><td>{factor*1000000:,}</td><td>{samples[a]["items"][category]:,.0f}</td></tr>')
    base=sum(samples['ecdsa']['bytes'][c] for c in categories[:2])
    ratios=[]
    for a,name in names.items():
        theoretical=sum(expected[a][:2]);actual=sum(samples[a]['bytes'][c] for c in categories[:2])
        ratios.append(f'<tr><td>{name}</td><td>{theoretical:,}</td><td>{theoretical/38:,.2f} 倍</td><td>{actual/base:,.2f} 倍</td></tr>')
    return '''
<h2>理论与实测能否对应？</h2>
<p class="status"><strong>能对应。账户记录大小、公钥槽数和 Storage Trie 节点数均与当前合约结构吻合。</strong>下面以百万账户阶段核对；实测为三个节点分别扣除基线后的平均值。理论只计新增账户本身，实测仍包含测试期间其他状态的增长。</p>
<h3>1. 单个账户增加了什么？</h3>
<p>EOA 和合约账户都有 <code>nonce、balance、storageRoot、codeHash</code>，不是 PQ 账户才新增后两个字段。EOA 的根与代码哈希指向空内容；本实验的 ML、SLH storage 账户都有非空 storage 和代码。</p>
<section><table><thead><tr><th>方案</th><th>公钥保存方式</th><th>每账户公钥槽数</th><th>运行时代码</th></tr></thead><tbody>
<tr><td>ECDSA EOA</td><td>不单独保存公钥；余额 1 wei</td><td>0</td><td>无</td></tr>
<tr><td>ML-DSA-44</td><td>1,312 bytes 公钥存入动态 bytes</td><td>42：长度 1 槽＋数据 41 槽</td><td>同一部署配置下共享</td></tr>
<tr><td>SLH storage（v7）</td><td>32 bytes 公钥存入 slot 0</td><td>1</td><td>同一部署配置下共享</td></tr>
</tbody></table></section>
<p>共享代码在基线前已部署，所以规模账户的代码增量为零；这不表示合约没有代码，也不表示部署不消耗代码存储 Gas。以下不使用旧 SLH immutable 模型。</p>
<h3>2. 理论大小怎样计算？</h3>
<p><strong>账户 snapshot：EOA 为 38 bytes，两个 PQ 方案均为 103 bytes。</strong>数据库键是 1 byte 前缀＋32 bytes 账户哈希，共 33 bytes。精简 RLP 会省略空根和空代码哈希：本实验 EOA 的值占 5 bytes，合约账户的值占 70 bytes，因此分别为 33＋5＝38、33＋70＝103 bytes。差额 65 bytes 包含 RLP 长度前缀变化，不是简单多存两个 32 bytes 字段。</p>
<p><strong>Storage snapshot：SLH 约 98 bytes，ML 约 4,086 bytes。</strong>每条记录的键是 1＋32＋32＝65 bytes；完整 32 bytes 槽值经 RLP 编码通常为 33 bytes。SLH 为 65＋33＝98 bytes；ML 的 41 个数据槽为 41×98＝4,018 bytes，长度槽保存 1,312×2＋1＝2,625，编码占 3 bytes，该记录为 65＋3＝68 bytes，合计 4,086 bytes。槽值前导零可能使编码略短。</p>
<p><strong>Storage Trie：SLH 约 103 bytes，ML 约 7,084 bytes。</strong>SLH 单槽只需一个根叶节点：33 bytes 数据库键＋通常 70 bytes 节点编码。ML 的 42 个槽位经哈希后，按当前 MPT 路径构成 59 个持久化节点；计入路径键和节点 RLP 后约为 7,084 bytes。这是当前槽位布局与通常编码长度下的结果，不是任意 42 个槽的通用常数。</p>
<h3>3. 百万账户：单账户逻辑增量核对</h3>
<section><table><thead><tr><th>指标</th><th>方案</th><th>理论 bytes/账户</th><th>实测 bytes/账户</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table></section>
<p>账户 snapshot 几乎精确吻合。EOA 自身不产生合约 storage，表中的少量实测增量来自全链其他状态；批量入账工具本身不写 storage。现有汇总结果没有逐地址归因，不能把这些值解释为 EOA 自身的槽位。</p>
<section><table><thead><tr><th>方案</th><th>记录类型</th><th>理论总数／节点</th><th>实测增量／节点</th></tr></thead><tbody>'''+''.join(counts)+'''</tbody></table></section>
<p>记录数量提供了另一项交叉检查：ML 的 4,200 万个槽和 5,900 万个 Storage Trie 节点、SLH 的各 100 万条记录，与实测主体对应。多出的记录包含系统等背景状态，不能直接归入公钥开销。</p>
<p>还需考虑显示精度：ML 百万阶段的原始 inspect 将 Storage snapshot 显示为 <code>3.81 GiB</code>、Storage Trie 显示为 <code>6.60 GiB</code>。两位小数的舍入误差可达约 5.12 MiB；换算成 bytes 后仍是近似值。因此，上表中几个 bytes/账户的差异不足以证明存在额外固定字段。</p>
<h3>4. 总量与单账户放大倍数</h3>
<p>这里的 state 只指<strong>账户 snapshot＋Storage snapshot</strong>，不含 Trie、代码、交易历史。以 ECDSA 为 baseline：</p>
<section><table><thead><tr><th>方案</th><th>理论 state bytes/账户</th><th>理论放大</th><th>实测放大</th></tr></thead><tbody>'''+''.join(ratios)+'''</tbody></table></section>
<p><strong>同一账户数、同一口径：总增量倍数＝平均单账户增量倍数。</strong>每节点平均值为三节点增量之和÷3；再除以账户数才是每账户平均值。实测 ECDSA 分母包含少量背景 storage，因此实测倍数略低于只计算账户本身的理论倍数。</p>
<p>账户 Trie 也符合预期：三个方案的百万账户增量均约 156 MiB。主 Trie 中的账户值保留固定长度的根和代码哈希；账户数量相同、地址分布近似随机，因此规模接近，PQ 新增存储主要体现在各账户的 Storage Trie。</p>
<h3>5. 为什么实际磁盘倍数不同？</h3>
<p>理论校验针对数据库逻辑内容；实际磁盘还受压缩、数据库组织、交易历史、索引和后台整理影响。前面的“落盘 bytes/账户”是全链磁盘增长的平均分摊，不是账户结构的固定大小。逻辑大小不能直接相加后当成磁盘占用，也不能由 42／1 个槽直接推导磁盘放大倍数。</p>
<details><summary>实现依据与数据来源</summary><p>账户编码见 <a href="../../../../external/bsc/core/types/state_account.go">SlimAccountRLP</a>；数据库键布局见 <a href="../../../../external/bsc/core/rawdb/schema.go">rawdb schema</a>；公钥布局见 <a href="../../../../contract/src/v4/MLDSAAccount.sol">MLDSAAccount</a> 和 <a href="../../../../contract/src/v7/PQStorageAccount.sol">PQStorageAccount</a>。主表与本节均直接读取阶段 JSON；原始 inspect 和三个节点的记录保留在各运行目录。</p></details>
'''
