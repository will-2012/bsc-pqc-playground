# ERC-4337 PQ 链路对比：v1–v6

2026-10-27，macOS arm64 / Apple M1 Pro 本地实测。这里比较真实 BSC 预编译与 ERC-4337 交易；[密码学基准](../crypto-bench/comparison.html)比较本机 Sign / Verify 接口，两者的实现与计时口径不同。v6 新增 SLH-DSA-SHA2-128f，旧版本与原始记录保留。

128f 与 128s 都是 NIST 类别 1。128f 显著加快签名生成，但签名从 7,856 bytes 增至 17,088 bytes；在这条链路里，验签和 calldata 费用更高，转账并不更便宜。

## 同链完整交易 gas

五条预编译路径使用同一 devnet-v6、EntryPoint v0.7、Alto；每种算法创建三个随机独立账户，再各转出 0.01 BNB。顺序提交，每笔外层交易一个操作。EOA 为三笔无 calldata、向已有无代码地址的转账。

| 路径 | 操作 | 平均外层 gas | 相对 EOA 转账 | 最低–最高 |
| --- | --- | ---: | ---: | ---: |
| 原生 ECDSA EOA | 转账 | 21,000 gas | baseline | 21,000–21,000 gas |
| SLH-128s v2 | 创建账户 | 745,077 gas | 35.48 倍 | 744,973–745,129 gas |
| SLH-128s v2 | 已部署账户转账 | 344,550 gas | 16.41 倍 | 344,490–344,610 gas |
| Falcon-512 v3 | 创建账户 | 1,339,768 gas | 63.80 倍 | 1,339,732–1,339,804 gas |
| Falcon-512 v3 | 已部署账户转账 | 169,742 gas | 8.08 倍 | 169,729–169,756 gas |
| ML-DSA-44 v4 | 创建账户 | 1,674,367 gas | 79.73 倍 | 1,674,179–1,674,527 gas |
| ML-DSA-44 v4 | 已部署账户转账 | 232,317 gas | 11.06 倍 | 232,145–232,457 gas |
| ML-DSA-65 v5 | 创建账户 | 2,154,846 gas | 102.61 倍 | 2,154,762–2,154,930 gas |
| ML-DSA-65 v5 | 已部署账户转账 | 294,377 gas | 14.02 倍 | 294,301–294,481 gas |
| SLH-128f v6 | 创建账户 | 1,022,133 gas | 48.67 倍 | 1,022,105–1,022,153 gas |
| SLH-128f v6 | 已部署账户转账 | 712,670 gas | 33.94 倍 | 712,410–712,830 gas |

创建包含 CREATE2、初始化、首次验签和一次向自身的零金额调用，不包含 faucet 充值及共享合约部署。表中是外层收据 gasUsed；EntryPoint 对账户扣费的 actualGasUsed / actualGasCost 另记在原始数据中。EOA 的 21,000 gas 已是完整转账，不能再加 ECRECOVER 的 3,000 gas。创建行也以一次 EOA 转账作分母，便于比较，并非同一种操作。

[本轮 v6 / EOA](v6/results.json) · [同链 v2](v6/slh-reference.json) · [同链 v3](v6/falcon-reference.json) · [同链 v4](v6/mldsa44-reference.json) · [同链 v5](v6/mldsa65-reference.json)

## 128s 与 128f 的取舍

| 项目 | v2：SLH-DSA-SHA2-128s | v6：SLH-DSA-SHA2-128f |
| --- | --- | --- |
| 标准 / 模式 | FIPS 205 Pure，空 context | FIPS 205 Pure，空 context |
| 安全类别 | NIST 类别 1 | NIST 类别 1 |
| 参数 | n=16，h=63，d=7，a=12，k=14，w=16 | n=16，h=66，d=22，a=6，k=33，w=16 |
| 私钥 / 公钥 | 64 bytes / 32 bytes | 64 bytes / 32 bytes |
| 签名 | 7,856 bytes | 17,088 bytes |
| 链上公钥 | immutable，嵌入账户代码 | 复用相同账户设计 |
| 原生输入 | 7,920 bytes | 17,152 bytes |
| 实验地址 | 0x0101 | 0x0105 |

两者都通过 FORS、WOTS+ 与多层 Merkle 树，将消息签名与公钥中的根绑定。128f 使用更多、更矮的子树，改变签名生成与验证所需的哈希工作量，以更大的认证材料换取更快的签名。f 表示 fast，s 表示 small；不是安全等级的高低。

合约和钱包层新增 v6 验证器 adapter 与 profile，复用 PQAccount / PQAccountFactory。账户固定 EntryPoint、公钥和验证器，没有 ECDSA owner、管理员、恢复、升级或换钥接口。工厂将公钥与 salt 绑定 CREATE2 地址；切换 profile 创建新身份，不迁移旧账户。历史接口名 ISLHDSA128s 只定义通用 verify ABI；算法由绑定的验证器决定。

## 两份 CPU 数据为什么不同

| 项目 | 密码学基准 | 本报告的预编译 CPU 测量 |
| --- | --- | --- |
| SLH 后端 | OpenSSL C / ARM SHA-256 | Trail of Bits Go v0.1.0 |
| 主指标 | 线程 CPU；Go GC 关闭 | 进程 CPU；Go GC 开启 |
| ECDSA 操作 | 已知公钥验签 | ECRECOVER，恢复公钥并计算地址 |
| 缓冲区与解析 | 预分配，复用密钥对象 | 按客户端 Run 路径解析输入并分配对象 |

因此，128s 验签的 194.56 / 408.31 us、128f 的 553.39 / 1,163.80 us，分别来自不同实现与计时方式。两份数据不支持“接入预编译后慢了两倍”的判断，也不能把密码学表的 ECDSA 倍数直接用于 gas 定价。

钱包端还有另一层实现：两种 SLH 使用 Noble JavaScript；Falcon / ML 使用 Go/cgo helper。负载中的准备耗时包含签名、RPC 估算等步骤，与两份原生 CPU 表都不同。

## 五轮原生 CPU 与定价

停止本地节点和 Bundler 后，使用同一组九个 32-byte 消息，轮转测量五轮共 125 组。128s 验签读取固定公开向量，其余验签路径在计时外生成随机密钥和签名。每组结果是多次调用的平均 CPU；表中再取五轮中位数或最大轮均值。

Go 单执行 P、锁定 OS 线程，正常启用 GC；主指标是客户端 Run 的进程 CPU，另存线程 CPU、墙钟与 Go 分配。签名计时不包含 keygen 或结果验签，C 分配不计入 Go B/op。这些数据衡量当前实现，不代表算法的最优实现。

| 路径 | 有效验签 CPU，中位数 | 已测路径最大轮均值 | 实验价格 |
| --- | ---: | ---: | ---: |
| ECRECOVER | 29.40 us | 30.66 us | 3,000 gas |
| SLH-128s | 408.31 us | 420.20 us | 60,000 gas |
| Falcon-512 | 9.24 us | 9.51 us | 10,000 gas |
| ML-DSA-44 | 15.43 us | 15.63 us | 10,000 gas |
| ML-DSA-65 | 25.78 us | 26.19 us | 10,000 gas |
| SLH-128f | 1,163.80 us | 1,203.07 us | 160,000 gas |

同一 Go SLH 后端原生签名：128s 387,569.33 us，128f 18,873.27 us；本轮 128f 约快 20.54 倍。钱包两种 SLH 都使用 Noble JavaScript，签名墙钟不能直接替代原生 CPU 数据。

v6 沿用原有定价公式：取有效签名及三类负例的最大轮均值，除以 ECRECOVER 的五轮中位数，乘 3,000 gas 和 1.25；先向上取整到 1,000，再到 10,000 gas。它依据已测路径的平均成本加余量，不是最慢单次调用或理论最坏情况上界。

本轮算得 154,000 gas，最终取 160,000 gas。10,000 gas 的取整档位使 Falcon / ML 同价，不表示它们比 ECRECOVER 慢。旧地址与价格保留；边界测试确认准确 gas 可执行、少 1 gas 失败。

校准二进制暂定 150,000 gas；报价确定后重建为 160,000 gas，两者仅价格常量不同，Run 实现未变。原生记录保留校准构建摘要，环境记录保存实际部署摘要。这是本地实验报价，不是主网定价结论。

[五轮原始数据、依赖与源文件摘要](v6/precompile.json) · [链上 STATICCALL 费用](v6/native-gas.json)

## 为什么签名更快，转账却更贵

预编译只减少验证计算；签名仍要通过外层 handleOps 的 calldata 上链。128f 多出 9,232 bytes，EIP-7623 的 calldata floor 会显著抬高交易最低 gas。签名随机字节造成小幅费用差异；价格应比较实测完整收据，不能只比较预编译固定 gas。

两种 SLH 都把 32 bytes 公钥嵌入账户代码，创建成本差异主要来自验签、签名数据及相应执行开销。Falcon / ML44 / ML65 将公钥保存在 bytes 存储，分别占 29 / 42 / 62 个槽，显著增加创建成本；但部署代码、初始化 calldata 和首次验证也各不相同。这里比较的是当前账户实现，不能把创建 gas 当成算法的固有成本。

| 已部署账户转账 | 外层 calldata | 平均 calldata floor | 平均实际 gas |
| --- | ---: | ---: | ---: |
| SLH-128s v2 | 8,580 bytes | 344,550 gas | 344,550 gas |
| SLH-128f v6 | 17,796 bytes | 712,670 gas | 712,670 gas |

这两组转账逐笔触及 [EIP-7623](https://eips.ethereum.org/EIPS/eip-7623) 的 calldata floor。对本次不含 7702 授权的普通外层交易，floor 为 `21,000 + 10 × (零字节数 + 4 × 非零字节数)`。因此，在 calldata 不变时，仅降低预编译价格不会让这组转账的外层 gas 下降；需要降低数据成本或改变打包方式。

## 有限负载与适用边界

- slh-dsa-v2：8 个账户 × 5 轮，40 次成功；5 笔外层交易；最高区块 gas 利用率 3.84%。
- slh-dsa-v6：8 个账户 × 5 轮，40 次成功；10 笔外层交易；最高区块 gas 利用率 7.01%。

每轮先准备签名和估算，再并发提交不同 sender。单独记录准备、提交窗口、收据等待，并以唯一外层交易计算 gas。结果包含钱包、RPC、模拟与打包时间；没有压满区块，不代表最大 TPS、生产容量或不同算法的纯 CPU 排名。

实测还发现 BSC 交易池拒绝 141,778-byte 的八操作 128f bundle，单笔交易上限为 131,072 bytes。v6 的 Alto 增加按真实 handleOps 编码大小拆包：calldata 预算 130,048 bytes，预留 1,024 bytes 给当前不含 7702 授权的交易封装；不会调大链的限制。八个并发操作可以拆成多笔外层交易。这个限制与区块 gas 上限是两回事。

[ERC-7562](https://eips.ethereum.org/EIPS/eip-7562)规定单个 packed、ABI 编码的 UserOperation 最大为 8,192 bytes。按钱包的编码口径，本轮已部署账户转账分别为 8,480 bytes（128s）和 17,696 bytes（128f），都超出此公共内存池规范；这不是 BSC 共识限制，也不表示所有 Bundler 都强制执行。

Alto 保持 safe-mode，并显式允许本地预编译；本地联通不等于公共池兼容。当前仅账户授权使用 PQ，Bundler 外层 ECDSA 交易、共识和 P2P 仍沿用原有机制；本实验不是整链抗量子改造。

[负载数据](v6/load.json) · [共同分叉观察](v6/fork-check.json) · [授权与密码学检查](v6/validation.json) · [环境、构建和收据审计](v6/environment.json)

## 版本与复现

v1 Solidity；v2 SLH-128s 预编译；v3 Falcon-512；v4 ML-DSA-44；v5 ML-DSA-65；v6 SLH-128f。五个原生地址在同一个 pqTime 启用，未编译实验标签、其他链 ID 或分叉前均不启用。原有账户和二进制不会被替换。

复现命令见 [tools/chain-bench](../../tools/chain-bench/README.md) 与 [one-box](../../one-box/README.md)。交叉复核运行 `python3 tools/chain-bench/audit.py`。原始样本、测量时源码摘要与构建信息保留不变；文档修订不冒充新一轮测量。运行目录与私钥不入 Git，当前完整客户端仅验证 macOS arm64。

历史结果：[v1](v1/results.json) · [v2](v2/results.json) · [v3](v3/results.json) · [v4](v4/results.json) · [v5](v5/results.json)。同链数据优先用于本轮比较，旧环境数据保持原样。

算法依据：[NIST FIPS 205](https://doi.org/10.6028/NIST.FIPS.205)、[FIPS 204](https://doi.org/10.6028/NIST.FIPS.204)。SLH 固定依赖 Trail of Bits go-slh-dsa v0.1.0，Falcon 固定 FALCON_NEON，ML 固定 mldsa-native v2.0.0；具体提交与文件摘要见原始数据。
