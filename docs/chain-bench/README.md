# ERC-4337 PQ 链路对比：v1 / v2 / v3 / v4 / v5

2026-10-27，macOS / Apple M1 Pro 本地实测。v1 是 SLH Solidity 验签；v2 是 SLH 预编译；v3 是 Falcon-512；v4 是 ML-DSA-44；v5 新增 ML-DSA-65。默认仍为 v1，旧环境、身份、原始结果保留。

v5 已完成签名、Bundler、三验证者、创建与转账。转账平均 294,313 gas，为 EOA 的 14.01 倍，比同链 ML-DSA-44 增加 26.73%。ML65 提供类别 3 参数，代价是更多计算、更大的公钥与签名；不会因为和 ML44 的预编译价格相同，完整交易就同价。

## 完整交易 gas

最新四算法对照均来自同一 devnet-v5、EntryPoint v0.7 和 Alto。每种算法随机创建三个独立账户，再各转出 0.01 BNB。顺序提交，实际每笔外层交易一个操作；EOA 基线为三笔无 calldata、向已存在无代码地址发送的 BNB 转账。

| 同链路径 | 操作 | 平均外层 gas | 相对 EOA 转账 | 最低–最高 gas |
| --- | --- | ---: | ---: | ---: |
| 原生 ECDSA EOA | 转账 | 21,000 gas | baseline | 21,000–21,000 gas |
| SLH v2 | 创建账户 | 745,133 gas | 35.48 倍 | 745,081–745,189 gas |
| SLH v2 | 已部署账户转账 | 344,560 gas | 16.41 倍 | 344,460–344,670 gas |
| Falcon v3 | 创建账户 | 1,339,757 gas | 63.80 倍 | 1,339,732–1,339,795 gas |
| Falcon v3 | 已部署账户转账 | 169,747 gas | 8.08 倍 | 169,705–169,768 gas |
| ML-DSA-44 v4 | 创建账户 | 1,674,323 gas | 79.73 倍 | 1,674,299–1,674,335 gas |
| ML-DSA-44 v4 | 已部署账户转账 | 232,237 gas | 11.06 倍 | 232,145–232,289 gas |
| ML-DSA-65 v5 | 创建账户 | 2,154,894 gas | 102.61 倍 | 2,154,786–2,154,966 gas |
| ML-DSA-65 v5 | 已部署账户转账 | 294,313 gas | 14.01 倍 | 294,229–294,397 gas |

创建包含 CREATE2、初始化、首次验签和一次向自身的零金额调用，不含 faucet 充值及共享工厂、验证器部署。表中使用实际外层收据 gasUsed；EntryPoint 对账户计费的 actualGasUsed / actualGasCost 是另一种口径。EOA 的 21,000 gas 已是完整转账，不应再加 ECRECOVER 的 3,000 gas。

| 历史独立环境 | 创建平均 | 转账平均 |
| --- | ---: | ---: |
| v1：SLH Solidity | 6,683,222 gas | 5,998,498 gas |
| v2：SLH 预编译 | 745,081 gas | 344,610 gas |
| v3：Falcon | 1,339,768 gas | 169,758 gas |
| v4：ML-DSA-44 | 1,674,235 gas | 232,225 gas |

历史数据没有覆盖。不同地址、随机签名字节和环境会造成少量 gas 差异，因此优先使用上方同链样本比较。

[本轮 ML65 与 EOA](v5/results.json) · [同链 ML44](v5/mldsa44-reference.json) · [同链 Falcon](v5/falcon-reference.json) · [同链 SLH](v5/slh-reference.json) · [历史 v1](v1/results.json) · [历史 v2](v2/results.json) · [历史 v3](v3/results.json) · [历史 v4](v4/results.json)

## 算法、编码与账户设计

| 项目 | v1 / v2：SLH | v3：Falcon | v4：ML-DSA-44 | v5：ML-DSA-65 |
| --- | --- | --- | --- | --- |
| 数学基础 | 哈希链与树 | NTRU 格 | 模格 | 模格 |
| 主要参数 | SHA2-128s；n=16、h=63、d=7、a=12、k=14、w=16 | n=512、q=12289 | k=4、l=4、n=256、q=8380417 | k=6、l=5、n=256、q=8380417 |
| 定义 / 封装 | FIPS 205 Pure、空 context | 原版 Falcon-512；不是 FN-DSA 候选编码 | FIPS 204 Pure、空 context | FIPS 204 Pure、空 context |
| NIST 安全类别 | 类别 1 | 类别 1 | 类别 2 | 类别 3 |
| 私钥编码 | 64 bytes | 1,280 bytes 主体，完整为 1,281 bytes | 2,560 bytes 展开私钥 | 4,032 bytes 展开私钥 |
| 公钥编码 | 32 bytes | 896 bytes 主体，完整为 897 bytes | 1,312 bytes | 1,952 bytes |
| 签名编码 | 7,856 bytes | 本实验接受 617–667 bytes | 2,420 bytes | 3,309 bytes |
| 公钥在链上 | immutable，嵌入代码 | bytes，29 个槽 | bytes，42 个槽 | bytes，62 个槽 |
| 创建 / 转账操作编码 | 8,576 / 8,480 bytes | 2,304 / 1,280 bytes | 4,480 / 3,040 bytes | 6,016 / 3,936 bytes |

SLH 用哈希链、树和一次性签名验证授权；安全假设较保守，但原始签名大。Falcon 借助私钥进行短向量采样，由公钥验证格上的关系；当前后端签名涉及浮点采样，验签使用整数运算。v3 省略公钥头 0x09、私钥头 0x59，原生层恢复；签名保留 0x39 头与随机 nonce。617–667 bytes 是本实验的编码与重试策略，不是所有 Falcon 实现的通用范围。

ML-DSA 的公钥给出带小噪声的模格关系。签名用短随机向量、哈希挑战和拒绝采样，证明掌握秘密并控制泄露；验证方检查模运算关系、向量范围与 hint 编码。ML65 增大矩阵和向量参数以提高安全等级，因此签名和验签都比 ML44 重。两者仍属于同一数学家族，组合使用不等于获得哈希与格之间的安全多样性。

所有账户只由固定 EntryPoint 验证授权、执行调用；固定公钥和验证器，无 ECDSA owner、管理员、恢复、升级或换钥接口。工厂将公钥与 salt 绑定 CREATE2 地址，重复创建复用已有账户。切换 profile 是新建身份，不会迁移旧账户。

ML65 的公钥存储为一个长度槽加 61 个数据槽；比 ML44 多 20 个槽。公钥只在创建时进入外层初始化参数，后续交易不重复携带，但验签时仍要从链上读取并传入预编译。大公钥提高创建与读取成本，大签名提高 calldata 成本。本版没有引入公钥代码存储、承诺或压缩优化。

ML44 / ML65 使用同一 [mldsa-native v2.0.0 提交](https://github.com/pq-code-package/mldsa-native/tree/834a90d5e846ffa1e1611bd24e160bb2e9b86d35)，clang -O3 -mcpu=apple-m1、OPT=1、AUTO=1；Go/cgo 包含两个独立命名空间，共用 OS 随机数桥接。算法依据为 [FIPS 204](https://csrc.nist.gov/pubs/fips/204/final)，不是旧 Dilithium 或 HashML-DSA。私钥列是展开编码，不能与 32-byte seed 备份混为一谈。

SLH 为固定 Go 后端，Falcon 为固定 [FALCON_NEON](https://github.com/GMUCERG/FALCON_NEON/tree/1d2670077d14de81a9b66b4c6494a67256342cd2) 后端。下面测量反映实际客户端实现，不是所有算法最佳实现的排名。

## 五轮 CPU 与预编译定价

本地节点和 Bundler 停止后测量。PQ 算法使用同一组九个 32-byte 消息，密钥在计时外随机生成；五轮轮转共 95 组，每组 benchtime 为 1 s。GOMAXPROCS=1、锁定 OS 线程、正常 GC。主指标是实际 Go precompile Run 的进程 CPU，另存线程 CPU、墙钟与 Go 分配；C 栈不计入 Go B/op。这里不包含 RPC、helper 启动、calldata、账户存储。

| 实际原生路径 | 进程 CPU 中位值及相对基线 | 固定验签价格 |
| --- | ---: | ---: |
| ECRECOVER 公钥恢复 | 29.92 us, baseline | 3,000 gas |
| SLH 验签 | 418.76 us, 13.99 倍 baseline | 60,000 gas |
| Falcon 验签 | 9.29 us, 0.31 倍 baseline | 10,000 gas |
| ML-DSA-44 验签 | 15.46 us, 0.52 倍 baseline | 10,000 gas |
| ML-DSA-65 验签 | 25.96 us, 0.87 倍 baseline | 10,000 gas |

原生签名 API：ML44 中位值 51.89 us，ML65 为 80.62 us，包含随机数和 Go 分配，不含 keygen、helper 进程启动。ECRECOVER 是公钥恢复，不能当作 ECDSA 签名时间，也不能直接等同于 crypto-bench 中的 ECDSA Verify。

沿用原定价规则：取五轮中所有已测有效和无效验签路径最慢一次的 CPU，除以 ECRECOVER CPU 中位值，乘 3,000 gas 和 1.25 余量；先向上取整到 1,000 gas，再到 10,000 gas。

| 算法 | 最慢已测验签 CPU | 取整到 1,000 gas | 最终实验定价 |
| --- | ---: | ---: | ---: |
| SLH | 426.311 us | 54,000 gas | 60,000 gas |
| Falcon | 9.442 us | 2,000 gas | 10,000 gas |
| ML-DSA-44 | 16.041 us | 3,000 gas | 10,000 gas |
| ML-DSA-65 | 26.375 us | 4,000 gas | 10,000 gas |

ML44 / ML65 同价来自既有的 10,000-gas 粒度，不能解释成计算量相同。旧价格保持不变。这是本地实验规则；跨硬件及更广的恶意输入上界仍未评估。正例、改签名、改消息、全零签名纳入测量，错误长度直接拒绝。

ML65 输入固定 5,293 bytes：publicKey[1952] || userOpHash[32] || signature[3309]。原生层完成 Pure 消息封装，context 为空；成功输出 32-byte 的 1，错误签名或长度输出 0，不修改输入。adapter 在调用失败、返回长度不对或结果不为 1 时拒绝，不回退其他算法。少 1 gas 与恰好定价的边界测试通过；链上 trace 核实四种预编译的实际调用 gas，并保存公开验签输入供复核。

[五轮原始测量](v5/precompile.json) · [实际调用 gas 与公开输入](v5/native-gas.json) · [历史 v4 测量](v4/precompile.json)

## 同链并发：ML44 与 ML65

同一链、同一 Bundler，先 ML44、后 ML65。每版 8 个独立账户 × 5 轮，共 40 个操作；账户创建与充值在计时外。每轮准备后，从不同 sender 并发提交，避免同一账户 nonce 争用。两版签名均通过 Go/cgo helper。

| 指标 | ML-DSA-44 v4 | ML-DSA-65 v5 |
| --- | ---: | ---: |
| 操作数 | 40 笔 | 40 笔 |
| 端到端耗时 | 15.07 s | 15.93 s |
| 准备阶段合计 | 1.28 s | 1.46 s |
| 提交窗口合计 | 13.79 s | 14.47 s |
| 端到端完成速率 | 2.66 ops/s | 2.51 ops/s |
| 提交到收据 p50 | 2.87 s | 2.87 s |
| 提交到收据 p95 | 2.88 s | 3.45 s |
| 外层交易 / 区块 | 5 / 5 | 5 / 5 |
| 最高区块 gas 利用率 | 2.43 % | 3.14 % |
| 平均外层 gas / 操作 | 204,496 gas | 266,738 gas |

80 笔操作全部成功。批量 gas 按唯一外层交易 hash 去重，避免把同一个 bundle 的 gas 为每个操作重复相加；收款方总余额变化已核对。准备、提交和收据时间分别记录，端到端包括密钥检查、签名、估算与出块等待。

这仍是有限负载测试，没有压满链，不代表最大 TPS 或长期容量。签名快慢不能直接换算成链吞吐，收据延迟还受 Bundler 调度与出块时机影响。旧 [SLH / Falcon 负载](v3/load.json) 与 [Falcon / ML44 负载](v4/load.json) 保留；前者 SLH 使用 JS 签名，不能把其端到端差异全归因于算法。

Alto 0.0.17 默认 max-gas-per-bundle 为 20M gas；旧 max-bundle-size / max-bundle-wait 字段不生效，已从模板移除。单笔 gas 对照依靠顺序发送。

[本轮分轮耗时、收据与区块数据](v5/load.json)

## 共同分叉与完整交易边界

v5 沿用 pqTime，在同一时间条件下启用 SLH 0x0101、Falcon 0x0102、ML44 0x0103、ML65 0x0104；没有新增另一套 PQ 分叉。仅 chain ID 714、Parlia、Jenner 后启用，排除 UBT。普通构建禁用；Rules、配置兼容性、顺序检查、override、EVM 和 RPC 注册使用一致条件。新环境三个验证者均实测分叉前关闭、分叉后有效向量通过。

Alto 保持 safe-mode，按环境标记只识别已启用的本地预编译。未知地址没有通用放行，公共预热地址表未改变；实际 gas 已包含调用开销。v1–v4 的独立二进制、配置与账户保留。

ML65 转账 calldata 为 4,036 bytes，EIP-7623 floor 为 162,460–162,880 gas；实际平均 294,313 gas，高于 floor，还承担公钥读取、EntryPoint 和执行成本。SLH 转账仍受大签名的 calldata floor 限制：21,000 + 10 × (零字节数 + 4 × 非零字节数)。预编译不压缩签名。

ML65 packed 且 ABI 编码的创建 / 转账操作为 6,016 / 3,936 bytes，低于 [ERC-7562](https://eips.ethereum.org/EIPS/eip-7562) 的 8,192-byte 限制。但首次 verificationGasLimit 为 2,677,714 gas，高于常见公共内存池的 500,000-gas 预算；转账为 249,735 gas。通过本地 Alto 不等于满足所有公共 Bundler 规则。

费用与 gas 分开。本轮 UserOperation 费率为 1 gwei，外层交易为 1.2 gwei；三笔 ML65 创建的账户扣费合计比外层成本少 0.001072403400 BNB，三笔转账多 0.000030847200 BNB。当前费率可能由本地执行账户补贴，不能作为生产盈利模型，也不能靠抬高验签 gas 掩盖费率差。


[四地址分叉前后记录](v5/fork-check.json)

## 验收、复现与结构

ML44 / ML65 上游功能测试和 KAT 摘要均通过；本地测试覆盖两组密钥 × 九个消息、篡改、长度、gas 边界、分叉 / RPC / 错误链、配置兼容性、普通构建禁用与 5 s fuzz。ML65 使用独立 OpenSSL ACVP-derived 公共空 context 向量，来源与校验和见 dependency.json；Noble 对三个消息与原生后端双向互验，并确认 ML44 不能授权 ML65。

真实账户测试覆盖创建、转账、Counter、签名 / 调用 / 跨链篡改、nonce 重放、直接 EOA 越权、profile 混用拒绝与三节点收据一致性。ML44 授权流程另外回归，同链四算法各三个账户创建与转账通过。18 项环境脚本测试通过。gas 与负载收据跨三验证者复核并确认最终性；重新构建的 v4 / v5 客户端与钱包 helper 均逐字节一致，结果见环境记录。旧版本 JSON 与 SLH 合约源码不覆盖。

[ML65 完整验收](v5/validation.json) · [构建、环境、源码与收据复核](v5/environment.json)

```text
contract/src/common,v1,v2,v3,v4,v5/  公共与独立版本合约
erc4337-wallet/common/              共用流程和账户验收
erc4337-wallet/v1,v2,v3,v4,v5/      版本配置、签名与算法验收
one-box/precompile/v2,v3,v4,v5/     原生扩展、桥接与测试
one-box/.runtime/                   忽略的密钥、数据、日志与构建产物
tools/chain-bench/common/           共用 CPU、gas、负载、trace、分叉检查
tools/chain-bench/v2,v3,v4,v5/       简单版本入口
docs/chain-bench/v1,v2,v3,v4,v5/     按版本归档的公开结果
```

v5 为独立 devnet-v5，RPC 19585 / 19587 / 19589、Alto 14377。固定 BSC v1.8.1、Alto 0.0.17、EntryPoint v0.7、Solidity 0.8.23 / Paris / optimizer runs 1,000,000、Go 1.26.4。整个 PQ 客户端当前仅在 macOS arm64 验证，Linux Intel 未接入。

版本指实验链路，不是 EntryPoint 标准版本；--version v1/v2/v3/v4/v5 默认 v1，--profile 保持兼容且拒绝冲突参数。v5 在独立 build/v5 中复用 v4 后端准备流程，共用随机数而不复制旧桥接实现。仅构建 v5 的新 checkout 也能通过显式参数调用旧 ML44 / Falcon，已有旧 helper 则优先保留原实现。

复现见 [chain-bench](../../tools/chain-bench/README.md)、[开发链](../../one-box/README.md)、[钱包](../../erc4337-wallet/README.md)、[合约](../../contract/README.md)。HTML 由 Markdown 和公开 JSON 生成；私钥、数据库、编译副本与完整日志不进入 Git 或报告。

本实验只保护用户账户授权，外层交易、共识与 P2P 仍使用原有密码学。标准化不表示本实现已获得认证或独立安全审计。
