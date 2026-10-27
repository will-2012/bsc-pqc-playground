# ERC-4337 PQ 链路对比：v1 / v2 / v3

2026-10-27，macOS / Apple M1 Pro 本地实测。v1 是 SLH 的 Solidity 完整验签；v2 是 SLH 原生预编译；v3 新增 Falcon-512 原生预编译。原有 v1、v2 环境和归档数据保留，默认仍为 v1。

v3 的结果是：转账更省 gas，但当前账户实现需要存储更大的公钥，创建账户反而更贵。原生验签快，并不意味着完整交易、钱包或 Bundler 都按同样倍数变快。

## 完整交易 gas

每版取三个独立账户，先创建，再各转出 0.01 BNB。EOA 基线是三笔无 calldata 的原生 BNB 转账。下表使用真实外层交易收据的 `gasUsed`，每笔交易包含一个操作，平均值取整。

| 路径 | 操作 | 平均外层 gas | 相对 EOA 转账 | 最低–最高 gas |
| --- | --- | ---: | ---: | ---: |
| 原生 ECDSA EOA | 转账 | 21,000 gas | baseline | 21,000–21,000 gas |
| v1：SLH / Solidity | 创建账户 | 6,683,222 gas | 318.25 倍 | 6,502,632–6,791,680 gas |
| v1：SLH / Solidity | 已部署账户转账 | 5,998,498 gas | 285.64 倍 | 5,736,926–6,237,303 gas |
| v2：SLH / 预编译 | 创建账户 | 745,081 gas | 35.48 倍 | 745,033–745,129 gas |
| v2：SLH / 预编译 | 已部署账户转账 | 344,610 gas | 16.41 倍 | 344,340–344,850 gas |
| v3：Falcon / 预编译 | 创建账户 | 1,339,768 gas | 63.80 倍 | 1,339,768–1,339,768 gas |
| v3：Falcon / 预编译 | 已部署账户转账 | 169,758 gas | 8.08 倍 | 169,729–169,792 gas |

v1、v2 行是此前独立环境的原始样本；v3 行来自新 `devnet-v3`，没有重写旧数据。为降低环境差异，新链上另外重测了三个 SLH v2 账户：创建平均 745,097 gas，转账平均 344,570 gas。在同链对照中，Falcon 转账减少 50.73%，创建增加 79.81%。

创建 gas 包含 CREATE2、账户初始化、验签和一次向自身的零金额调用；不包含 faucet 充值或共享工厂、验证器的初始部署。EOA 和 PQ 转账接收方均已存在且无代码。21,000 gas 的 EOA 转账不是一次 ECRECOVER 预编译调用，不能另加 3,000 gas。

[历史 v1](v1/results.json) · [历史 v2](v2/results.json) · [v3 与 EOA](v3/results.json) · [同链 SLH 对照](v3/slh-reference.json)

## 算法、编码与账户设计

| 项目 | v1 / v2：SLH | v3：Falcon |
| --- | --- | --- |
| 算法 / 参数 | SLH-DSA-SHA2-128s，FIPS 205 Pure、空 context | 原始 Falcon-512，NTRU 格，n=512、q=12289 |
| 私钥编码 | 64 bytes | 1,280 bytes 主体；完整编码为 1,281 bytes |
| 公钥编码 | 32 bytes | 896 bytes 主体；完整编码为 897 bytes |
| 签名编码 | 固定 7,856 bytes | 本实验接受 617–667 bytes 的压缩签名，头字节 0x39 |
| 公钥在链上 | immutable，嵌入账户代码 | bytes 状态变量，896 bytes 数据加长度槽，共 29 个槽 |
| 首次 / 转账操作编码 | 8,576 / 8,480 bytes | 本次样本 2,304 / 1,280 bytes |
| 账户控制 | 固定公钥和验证器，无 ECDSA owner、恢复或升级接口 | 相同权限边界；公钥仅构造时写入，没有修改接口 |

v3 沿用 crypto-bench 已测的 TRON 风格主体编码：省略公钥固定头 `0x09`、私钥固定头 `0x59`，原生后端在调用 Falcon API 前恢复。签名保留 `0x39` 头和随机 nonce。617–667 bytes 是本实验采用的编码与重试策略，不是所有 Falcon 实现的通用长度范围；不是 Falcon-det，也不是 FN-DSA 候选编码。

Falcon 可以通俗理解为：公钥给出一个格上的关系，私钥帮助找出满足该关系的足够短的向量。签名者使用随机采样，验证者通过 SHAKE256、整数模运算和长度约束检查关系；高效验签使用 NTT。这个后端的签名实现涉及浮点采样，验签走整数运算路径。签名工程的侧信道与浮点实现质量仍需单独审查。

后端固定为 [FALCON_NEON](https://github.com/GMUCERG/FALCON_NEON/tree/1d2670077d14de81a9b66b4c6494a67256342cd2)，与 crypto-bench 同源、同参数。钱包和客户端均通过 Go/cgo 调用；每次密钥生成与签名使用 OS 随机数，不调用上游测试用 `get_seed`。参考 [Falcon API 的编码说明](https://falcon-sign.info/impl/falcon.h.html)。目前此后端只在 macOS arm64 集成和验证，Linux x86_64 尚未接入。

大公钥解释了创建代价：v3 要初始化 29 个存储槽，之后每次验证也要读取公钥。小签名减少外层 calldata，但没有消除这部分状态访问。当前版本先保持实现直观，尚未采用公钥放入独立代码、压缩或承诺等优化。

## 原生性能与预编译定价

最终轮次在本地链和 Bundler 停止后测量。使用同一组九个 32-byte 消息，轮转五轮，共 45 组测量，包含 ECRECOVER、SLH 正负例、Falcon 正负例及签名。每组 benchtime 为 1 s；GOMAXPROCS=1、锁定 OS 线程、正常 GC。主要计量为进程 CPU，另存线程 CPU、墙钟与 Go 堆分配；C 栈不计入 Go 的 B/op。

| 实际原生路径 | 进程 CPU 中位值 | 相对 ECRECOVER | 固定验签价格 |
| --- | ---: | ---: | ---: |
| ECRECOVER 公钥恢复 | 30.01 us | baseline | 3,000 gas |
| SLH 有效验签 | 415.60 us | 13.85 倍 | 60,000 gas |
| Falcon 有效验签 | 9.49 us | 0.32 倍 | 10,000 gas |
| Falcon 错误消息验签 | 9.48 us | 0.32 倍 | 10,000 gas |

Falcon 原生签名 CPU 中位值为 167.66 us。该值是 Go/cgo 签名调用，排除密钥生成、进程启动、Node.js、RPC 和 Bundler；不是完整钱包接口的耗时。预编译性能比较的是实际 `Run` 路径，包含解析、验签和返回，不包含 EVM、RPC 或交易 calldata。ECRECOVER 是公钥恢复，不是已知公钥验签。

两种 PQ 预编译采用同一规则：五轮中有效或无效验签最慢样本 ÷ 同轮 ECRECOVER 中位值 × 3,000 gas × 1.25，再先向上取整到 1,000 gas、后向上取整到 10,000 gas。

- Falcon：9.625 us ÷ 30.013 us × 3,000 × 1.25，千位报价 2,000 gas，最终 10,000 gas。
- SLH：429.956 us ÷ 30.013 us × 3,000 × 1.25，千位报价 54,000 gas，最终仍为 60,000 gas。

Falcon 的万位取整带来了较大的额外余量。10,000 gas 不代表它在这台机器上比 ECRECOVER 更慢；这是沿用 v2 实验取整规则的结果。有效、无效及长度不符输入都按各自固定价格收费。STATICCALL trace 已确认 SLH / Falcon 实际扣除 60,000 / 10,000 gas。

编译使用 `-O3 -mcpu=apple-m1 -DFALCON_LOGN=9 -DAPPLE_M1=0 -fno-fast-math -ffp-contract=off`，保留 NEON，关闭相关融合浮点特化。源归档、提交、校验和、构建脚本、客户端与测试二进制摘要随结果记录。独立 crypto-bench 的封装与测量路径不同，不能要求数值完全相等。

这是单台 Mac、固定后端和有限输入支持的本地实验价，不是最坏成本的形式上界，也不是 BSC 主网价格。跨硬件、安全审计和资源上限验证仍未完成。

[最终五轮数据及构建记录](v3/precompile.json) · [链上预编译扣费检查](v3/native-gas.json) · [历史 v2 原生数据](v2/precompile.json)

## 同一个硬分叉启用两个预编译

v3 客户端新增 `ChainConfig.pqTime`、`Rules.IsPQ`、配置兼容性检查、分叉顺序检查和 `--override.pq`。仅本地 Parlia / chain ID 714，在 Jenner 后且时间达到 `pqTime` 时同时启用以下两个地址，UBT 模式排除。普通构建不启用实验预编译。

| 地址 | 验签 | 原生输入 | 返回 |
| --- | --- | --- | --- |
| 0x0101 | SLH-DSA-SHA2-128s | 公钥 32 bytes + 消息 32 bytes + 签名 7,856 bytes | 32-byte 0/1 |
| 0x0102 | Falcon-512 | 公钥主体 896 bytes + 消息 32 bytes + 签名 617–667 bytes | 32-byte 0/1 |

本地首次启动时，PQ 分叉安排在 Jenner 后 5 s，并持久保存。三个验证者均实测到：分叉前两个地址返回空数据；分叉后两个地址均对有效向量返回 1。EVM 与 RPC 使用同一启用条件，不修改公共分叉 map 或预热地址表，首次冷调用另承担正常地址访问成本。

这里说的“同一硬分叉”是指 v3 实验客户端。独立旧 v2 客户端继续沿用原来的 Jenner 启用方式，没有修改已存在的 v2 环境或创世配置。地址不是主网标准预编译。

Alto 保持 safe-mode；v3 环境识别两个 stateless 预编译地址，旧 v2 环境只识别 SLH，v1 都不启用。EntryPoint v0.7 保持不变。

[三验证者真实切换记录](v3/fork-check.json)

## 钱包、Bundler 与链的并发测试

同一条 devnet-v3、同一个 Alto、同一 EntryPoint，先测 SLH，后测 Falcon。每版八个独立账户，五轮；每轮先准备八个操作，再同时提交，下一轮等待本轮全部成功。初始部署和充值不计入窗口，钱包签名、模拟、打包及收据等待计入端到端时间。

| 指标 | SLH v2 | Falcon v3 |
| --- | ---: | ---: |
| 成功操作 | 40 笔 | 40 笔 |
| 账户数 × 轮数 | 8 × 5 | 8 × 5 |
| 准备阶段合计 | 115.06 s | 1.63 s |
| 端到端总时长 | 127.15 s | 13.30 s |
| 端到端吞吐 | 0.31 ops/s | 3.01 ops/s |
| 提交窗口吞吐 | 3.31 ops/s | 3.43 ops/s |
| 提交到收据 p50 | 2.37 s | 2.31 s |
| 提交到收据 p95 | 2.58 s | 2.92 s |
| 外层交易数 | 5 笔 | 5 笔 |
| 最高区块 gas 利用率 | 3.79 % | 1.72 % |
| 批量平均外层 gas / 操作 | 324,684 gas | 140,732 gas |

每版 40 笔操作全部成功，分别实际装入五笔外层交易、五个区块，每笔外层交易八个操作。批量 gas 按唯一外层交易去重后相加，不能把同一个 bundle 的 gas 为每个 UserOperation 重复相加。

准备阶段包含密钥与账户检查、stub 签名、估算、正式签名和本地验签。SLH 当前钱包使用 noble JavaScript，Falcon 使用原生 Go helper，两版实现路径不同。因此端到端差异不能全归因于算法，更不能宣称链吞吐提升近十倍。排除准备阶段后，提交窗口的吞吐和收据等待接近，主要受当前 Bundler 调度和出块等待影响。

最高区块 gas 利用率只有约 3.79% / 1.72%，没有压满链。这是有限并发联通与成本测试，不是最大 TPS、长期稳定性或硬件容量结论。三验证者最终性已覆盖本次全部样本。

本次还确认：Alto 0.0.17 不识别旧模板的 `max-bundle-size`、`max-bundle-wait`，两项无效配置已移除。原始负载 JSON 保留测试时的配置快照，其中旧字段不表示实际生效；实际 gas 打包上限由 `max-gas-per-bundle` 控制，本轮为默认 20M gas。原有单笔报告是顺序提交得到的单操作交易，不是上述无效字段保证的。

[并发原始结果、分轮耗时与区块数据](v3/load.json)

## 如何理解完整交易成本

SLH 的三笔同链转账仍受 EIP-7623 calldata floor 约束：`21,000 + 10 × (零字节数 + 4 × 非零字节数)`。预编译没有压缩 7,856-byte 签名。Falcon 的三笔转账 gas 高于各自 floor，已经主要体现账户存储读取、EntryPoint、合约调用和验签等执行成本。

- `outerTransactionGas` 是外层真实交易 gas，供本文比较。
- `actualGasUsed` / `actualGasCost` 是 EntryPoint 对账户的计费，不等于外层 gas 或 Bundler 成本。
- 价格与 gas 必须分开看。本轮 UserOperation 为 1 gwei，Bundler 外层为 1.2 gwei；三笔单操作 Falcon 转账的账户扣款合计比外层费用少约 0.0000493218 BNB，由本地测试执行账户补足。当前费率配置不能直接作为生产收费方案，不能靠提高验签 gas 掩盖这个费率差。
- PQ 公钥只在创建时进入外层初始化参数；后续转账不重复携带公钥，但账户验证会从链上读取它并传给预编译。

Falcon 的操作编码解决了本地 SLH 的大小预算问题：本次首次 / 转账为 2,304 / 1,280 bytes，低于 [ERC-7562](https://eips.ethereum.org/EIPS/eip-7562) 对 packed 且 ABI 编码操作的 8,192-byte 限制。但首次验证预算约 1,677,956 gas，高于常见公共内存池的 500,000 gas 预算；已部署账户约 144,998 gas。大小合规不等于满足所有公共内存池规则，目前仍使用适配本地预编译的 Bundler。

## 结构、验收与复现

v1、v2、v3 是实验链路版本，不是 EntryPoint 或算法标准的版本。入口 `--version v1/v2/v3`；旧 `--profile` 继续兼容，冲突参数直接拒绝。

```text
contract/src/common/          原有共享账户、工厂、测试合约
contract/src/v1/              SLH Solidity 验签
contract/src/v2/              SLH 预编译适配器
contract/src/v3/              Falcon 账户、工厂、预编译适配器
erc4337-wallet/common/        通用钱包流程及账户验收
erc4337-wallet/v1,v2,v3/      独立版本配置；v3 有原生签名与密码学验收
one-box/precompile/v2/       保留 SLH 扩展
one-box/precompile/v3/       Falcon 与共同 PQ 分叉扩展
tools/chain-bench/common/     共用链路及原生性能测试流程
tools/chain-bench/v2,v3/      版本入口和专有工具
docs/chain-bench/v1,v2,v3/    按版本归档公开结果
```

v3 使用独立 devnet-v3，RPC 19565 / 19567 / 19569、Alto 14357；旧环境保持原端口和部署记录。依赖固定 BSC v1.8.1（947ba1cb…）、Alto 0.0.17（1d330b71…）、EntryPoint v0.7、Solidity 0.8.23 / Paris / optimizer runs 1,000,000、Go 1.26.4、Node.js v25.9.0。

已通过：两个独立 Bouncy Castle 公共 KAT、随机签名与编码负例、gas 边界、共用分叉前后与 RPC 条件、普通构建禁用、params 测试，以及 5 s fuzz。真实创建、转账、Counter、签名 / 调用 / 跨链篡改、nonce 重放、直接 EOA 越权和三验证者一致性均通过。18 项环境脚本测试通过，v1 创建 / 转账回归通过；v2 重构建二进制与旧环境逐字节一致。原始 v1、v2 JSON 和 SLH 合约字节码保留；原始 v2 测量中的 `one-box/precompile/...` 路径现在对应 `one-box/precompile/v2/...`，文件内容未改。

复现步骤见 [chain-bench 工具](../../tools/chain-bench/README.md)、[开发链](../../one-box/README.md)、[钱包](../../erc4337-wallet/README.md) 和 [合约](../../contract/README.md)。所有密钥、数据库、编译副本和完整运行日志都在 Git 忽略的 `one-box/.runtime/`，不进入报告。

PQ 只保护本实验的用户账户授权；外层交易、共识和 P2P 仍沿用原有密码学。实现未经独立安全审计，当前不作为生产钱包或主网升级方案。
