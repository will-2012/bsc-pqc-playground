# ERC-4337 链路 Bench：v1 / v2

2026-10-27，macOS / Apple M1 Pro 本地实测。v1 使用 Solidity 完整验签；v2 新增 SLH-DSA-SHA2-128s 预编译。算法、公钥、签名格式和账户授权逻辑保持一致。

## 版本和代码结构

v1、v2 是本仓库的实验链路版本，不是 ERC-4337、EntryPoint 或密码学标准的版本。两版都使用 EntryPoint v0.7 和同一 SLH 参数。

```text
contract/src/common/          共享账户、工厂和测试合约
contract/src/v1/              Solidity 完整验签
contract/src/v2/              预编译适配器
erc4337-wallet/common/        共享钱包流程、SLH 签名与验收
erc4337-wallet/v1/            v1 配置
erc4337-wallet/v2/            v2 配置与预编译探测
tools/chain-bench/common/     共享链上压测流程
tools/chain-bench/v2/         v2 独有的原生预编译测试
one-box/precompile/v2/       v2 客户端扩展及公开向量
docs/chain-bench/v1/          v1 原始结果
docs/chain-bench/v2/          v2 与 EOA 原始结果、原生测试结果
```

入口使用 `--version v1/v2`，仍兼容原来的 `--profile`。以后新增 v3、v4 时，只新增版本差异并注册，不复制公共实现。原有 profile ID、运行目录、私钥和部署记录保持不变。

## 完整交易 gas

每种 PQ 版本均取三个独立账户：先创建账户，再各转出 0.01 BNB。EOA 基线为三笔原生 BNB 转账。表中使用外层交易收据的 `gasUsed`，平均值取整。

| 路径 | 操作 | 平均 gas | 相对 EOA 转账 | 最低–最高 gas |
| --- | --- | ---: | ---: | ---: |
| 原生 ECDSA EOA | 转账 | 21,000 gas | baseline | 21,000–21,000 gas |
| v1：Solidity | 创建 PQ 账户 | 6,683,222 gas | 318.25 倍 | 6,502,632–6,791,680 gas |
| v1：Solidity | 已部署账户转账 | 5,998,498 gas | 285.64 倍 | 5,736,926–6,237,303 gas |
| v2：预编译 | 创建 PQ 账户 | 745,081 gas | 35.48 倍 | 745,033–745,129 gas |
| v2：预编译 | 已部署账户转账 | 344,610 gas | 16.41 倍 | 344,340–344,850 gas |

本次样本中，v2 相对 v1 的创建 gas 减少约 88.85%，转账减少约 94.26%。v1 原始结果保持不变，v2 是独立环境的新样本，不是同一笔交易的重放。

创建行包含 CREATE2 创建 PQAccount、验签和一次向自身的零金额调用；不包含 faucet 充值，也不包含共享验证器和工厂的初始部署。EOA 和 PQ 转账的收款地址均已存在且无代码，转账无业务 calldata。EOA 的 21,000 gas 不是调用 ECRECOVER 预编译的费用，不能再加上 3,000 gas。

[ v1 原始数据 ](v1/results.json) · [ v2 与 EOA 原始数据 ](v2/results.json)

## 预编译性能与价格

比较的是 BSC 实际预编译 `Run` 路径：包括输入解析、密码学计算和返回结果；不含 RPC、EVM 调用、交易 calldata 或钱包签名。ECDSA 基准是公钥恢复，不是已知公钥验签。

使用同一组 32 bytes 消息，SLH 数据含 8 组 noble 签名和 1 组 OpenSSL 独立签名。五轮轮转测试，单个 Go 执行 P，GC 开启。主要计量为进程 CPU，包含 Go GC；线程 CPU、墙钟、内存和分配次数另行保存。

| 实际预编译路径 | 进程 CPU 中位值 | 相对 ECRECOVER | 固定价格 |
| --- | ---: | ---: | ---: |
| ECRECOVER | 29.81 us | baseline | 3,000 gas |
| SLH-DSA：有效签名 | 410.98 us | 13.79 倍 | 60,000 gas |
| SLH-DSA：篡改签名 | 412.89 us | 13.85 倍 | 60,000 gas |
| SLH-DSA：全零签名 | 418.06 us | 14.02 倍 | 60,000 gas |

定价计算：五轮中 SLH 最慢样本 422.943 us ÷ ECRECOVER 中位值 29.812 us × 3,000 gas × 1.25 余量，向上取整约 54,000 gas，再取整到 60,000 gas。60,000 gas 是 ECRECOVER 价格的 20 倍，其中包含上述实验余量和取整。价格编译为客户端常量，不在运行时根据机器速度变化。单位边界测试和链上 adapter 的 STATICCALL trace 均确认实际扣除 60,000 gas。

这是单台 Mac、固定后端、有限样本支持的本地实验价，不是密码学最坏输入成本的形式上界。正式网络定价仍需不同硬件、更多输入和资源上限验证。最初的墙钟测试受桌面调度影响，最终测试补充了 CPU 计量；不能把等待时间全部当作计算成本。

后端固定 [go-slh-dsa v0.1.0](https://github.com/trailofbits/go-slh-dsa/tree/v0.1.0)，提交 `0b3b31f8…`，由 Go module 校验和锁定。ECRECOVER 使用 BSC 原有实现。参考 [EIP-7951](https://eips.ethereum.org/EIPS/eip-7951) 的相对 ECRECOVER 性能定价方法；这不是已通过的 SLH 预编译提案。

原生测试原始结果见 [v2/precompile.json](v2/precompile.json)。

## 为什么转账仍约 34.5 万 gas

v2 三笔转账的实际 gas 均等于从真实外层 calldata 计算的 EIP-7623 floor：`21,000 + 10 × (零字节数 + 4 × 非零字节数)`。签名仍为 7,856 bytes，预编译没有压缩签名。

因此，60,000 gas 是验签预编译的价格，完整交易还承担 calldata、EntryPoint、账户执行等成本；本次转账最终受 calldata floor 约束。首次创建还有账户代码部署成本，未触及这个 floor。

计量口径：

- `outerTransactionGas`：真实外层交易 gas，用于上表。
- `actualGasUsed` / `actualGasCost`：EntryPoint 对账户的计费，不等于外层 gas 或 Bundler 成本。
- v2 原始数据额外保存 `outerGasPrice`、`outerTransactionCost`、calldata 字节数和 floor，便于独立复算。本地 UserOperation 与 Bundler 的实际 gas price 分别为 1 / 1.2 gwei，不直接用金额比较算法。
- 端到端耗时包含钱包签名、安全模拟和打包等待。v1 创建约 62 s、转账约 57–62 s；v2 创建约 4.8 s、转账约 3.5–4.1 s。这是顺序联通实验，不是 CPU 或吞吐极限测试。

## v1 / v2 的边界

| 项目 | v1 | v2 |
| --- | --- | --- |
| wallet profile | `slh-dsa-v1`，仍为默认 | `slh-dsa-v2`，显式选择 |
| 账户 / 工厂 | 原 PQAccount / PQAccountFactory | 复用同一源码，另行部署并绑定新验证器 |
| 验证器 | `SLHDSA128s` Solidity | `SLHDSA128sPrecompile` adapter → 本地 `0x0101` |
| 客户端 | 原固定 BSC 构建 | 同一固定源码，加显式 `pqprecompile` 构建标签 |
| 启用条件 | 无新增预编译 | 仅 Parlia、chain ID 714、Jenner 后，排除 UBT |
| 运行环境 | `devnet`，RPC 19545，Alto 14337 | `devnet-v2`，RPC 19555，Alto 14347 |
| 密钥与部署 | `wallet/slh-dsa-v1/` | `wallet/slh-dsa-v2/`，不会覆盖 v1 |

BSC v1.8.1（`947ba1cb…`）、Alto 0.0.17（`1d330b71…`）、EntryPoint v0.7、Solidity 0.8.23 / Paris / optimizer runs 1,000,000、Node.js v25.9.0、noble 0.7.1。v2 原生构建使用 Go 1.26.4、darwin/arm64、CGO 开启。

v2 不修改公共分叉 map 和预热地址表，首次 adapter 调用会承担正常冷地址访问成本。Alto 只在 v2 环境将 `0x0101` 加入 stateless 预编译识别，`safe-mode` 保持开启。v1 进程不启用该识别。

两版的首次操作 / 转账编码均为 8,576 / 8,480 bytes，仍超出 [ERC-7562](https://eips.ethereum.org/EIPS/eip-7562) 公共内存池的 8,192-byte 限制。v2 首次操作的验证预算约 763,894 gas，已部署账户约 154,519 gas。不能把本地 Alto 接收请求解释成符合公共内存池全部规则。

## 验收与复现

v2 已通过：noble / OpenSSL 独立签名互验、消息 / 公钥 / 签名 / 长度负例、gas 边界、启用条件、RPC map 一致性、普通构建禁用检查，以及短时 fuzz。真实账户创建、转账、Counter、参数篡改、跨链签名、重放、EOA 越权和三验证者一致性检查均通过。

目录整理后，五个合约的 ABI、创建字节码和运行字节码与整理前一致；编译器的源码位置索引随文件路径改变。原始测量 JSON 只移动归档位置，不改测量值。原生测试 JSON 中的历史 `sources` 路径 `one-box/precompile/...` 现对应 `one-box/precompile/v2/...`，文件内容及 SHA-256 不变。

目录整理后的回归检查：17 项环境脚本测试通过；版本选择、冲突参数和模块加载检查通过；v1、v2 分别重新创建一个账户并转账成功，v2 同时复验 EOA 基线。v2 从新目录重新构建后的二进制与此前实测版本逐字节一致。上述回归样本仅用于验收，不混入表中的三账户统计。

v1 的行为、默认配置和旧结果保留。完整验收与运行日志仍在被 Git 忽略的 `one-box/.runtime/<环境名>/`，本目录仅归档公开结果。

[运行步骤](../../tools/chain-bench/README.md) · [钱包与版本选择](../../erc4337-wallet/README.md) · [客户端构建与环境](../../one-box/README.md) · [合约说明](../../contract/README.md)

PQ 仅用于用户账户授权；外层交易、共识和 P2P 仍沿用原有密码学。实验客户端和合约未经独立安全审计。
