# 账户存储实验

ECDSA EOA、ML-DSA-44 和 SLH-DSA-SHA2-128s 的 1 万、10 万、100 万账户实验均已完成。

- [主报告](local-20261030/report.html)：ECDSA、ML 和最新 SLH storage（v7），展示每节点平均值及理论核对。
- [四种模型对比](v7-20261031/comparison.html)：额外保留旧 SLH immutable，比较 state、代码、Trie 和实际磁盘。
- [实验标准与复现](../../../tools/chain-bench/storage/README.md) · [核查方法与多机改进](analysis.md)。

## 数据来源

| 运行 | 内容 |
| --- | --- |
| `local-20261030` | ECDSA、ML、旧 SLH immutable 的原始测量 |
| `v7-20261031` | SLH storage 的正式阶梯测量 |
| `v7-smoke` | 16/32 账户与真实 UserOperation 的功能验证，不用于规模结论 |

主报告通过 `report-sources.json` 选用最新 SLH 数据，原始 manifest、阶段 JSON、inspect 输出和批次记录不变。每节点独立测量，报告先扣除各自基线，再取三节点平均。

规模账户通过 Factory 批量创建，每账户持有 1 wei；少量 UserOperation 在基线前验证。规模账户尚未执行 UserOperation，不含其首次使用后的 EntryPoint nonce、deposit 等增量。所有组保留历史、校验状态根，并采用相同落盘步骤。

[v7 阶段报告](v7-20261031/report.html) · [v7 功能验证](v7-smoke/report.html) · [v7 使用说明](../../../tools/chain-bench/v7/README.md)

公开记录中的本机路径与主机名已脱敏：`${REPO}` 表示仓库根目录，`${HOME}` 表示用户目录，`${HOST}` 表示测试主机。命令和日志保留参数结构；测量值与测量时源码哈希不变。实际运行配置仅保存在 Git 忽略的运行目录。
