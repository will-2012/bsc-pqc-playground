# ERC-4337 PQ 钱包

本地实验钱包：随机生成 PQ 密钥，通过 Alto 和 EntryPoint 创建智能账户，再发送 BNB 或调用合约。各版本均使用 EntryPoint v0.7：

| profile | 验签方式 | 本地环境 |
| --- | --- | --- |
| `slh-dsa-v1`，默认 | Solidity 完整验签 | 原 `devnet`，不需要新增预编译 |
| `slh-dsa-v2` | SLH adapter 调用原生预编译 | 独立 `devnet-v2`，或 v3–v5 共同 PQ 分叉环境 |
| `falcon-v3` | Falcon-512 原生预编译 | 独立 `devnet-v3`，或 v4/v5 环境 |
| `mldsa-v4` | ML-DSA-44 原生预编译 | 独立 `devnet-v4`，或 v5 环境 |
| `mldsa65-v5` | ML-DSA-65 原生预编译 | 独立 `devnet-v5` |

## 使用

从仓库根目录执行：

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq

# 新密钥、新地址；自动部署缺少的共享合约，充值 2 BNB，再通过 UserOperation 创建账户
./tools/erc4337-wallet create devnet

# 使用 create 输出的 id，向一个被动接收地址转账
./tools/erc4337-wallet transfer devnet <id> <接收地址> 0.01

# 正负例、OpenSSL 互验、真实交易和三节点一致性检查
./tools/erc4337-wallet test devnet

./tools/one-box down
```

仅部署共享合约可执行 `./tools/erc4337-wallet deploy devnet`。批量实验与 gas 结果见 [chain-bench](../tools/chain-bench/README.md)。已有 Bundler 正在运行时，先执行 `./tools/one-box bundler down` 再重新构建。

SLH profile 的 `test` 额外需要支持 SLH-DSA 的 OpenSSL 3.5+，可用 `OPENSSL=/path/to/openssl` 指定。日常创建、转账不依赖 OpenSSL。Node 依赖由钱包自身的 `package-lock.json` 锁定，`bundler build` 会安装。

`--pq` 只修改当前本地环境的 Bundler 预算，保留 `safe-mode`。配置会保存，重启无需重复传入；已有 Bundler 使用 `./tools/one-box bundler restart --pq`。纯 Solidity 验签不符合公共内存池的标准 gas 预算，只用于独立的本地 PQ Bundler。不要同时启动多个钱包命令争用同一个 faucet；bench 已按顺序调用。

账户创建、转账各自是一笔真实 UserOperation，均经钱包签名、Bundler 模拟、外层交易、EntryPoint 授权验证和执行。faucet 的普通转账仅用于提供本地测试资金，不参与 PQ 授权。没有 Paymaster：账户支付 UserOperation 费用，Bundler 垫付外层交易后由 EntryPoint 结算。

## 版本与扩展

入口支持 `--version v1/v2/v3`，默认 v1。原 `--profile slh-dsa-v1/slh-dsa-v2` 保持兼容；同时指定时必须一致，否则拒绝执行：

```bash
./tools/erc4337-wallet create devnet --version v1
```

| 文件 | 职责 |
| --- | --- |
| `common/wallet.mjs` | 环境校验、本地充值、部署记录、账户保存、准备与提交操作、等待收据 |
| `common/slh-dsa.mjs` | 两版共享的 SLH 密钥、签名、账户 ABI 与操作编码 |
| `v1/profile.mjs`、`v2/profile.mjs` | 版本身份、验证器部署选择及版本特有检查 |
| `profiles.mjs` | 显式注册允许的 profile；未知版本直接拒绝 |
| `cli.mjs` | 创建、转账、部署和验收入口 |
| `common/test.mjs` | 当前 profile 的密码学和全链路验收 |

v2 在完成 [环境准备](../one-box/README.md#v2-预编译实验环境) 后显式选择：

```bash
./tools/erc4337-wallet create devnet-v2 --version v2
./tools/erc4337-wallet transfer devnet-v2 <id> <接收地址> 0.01 --version v2
./tools/erc4337-wallet test devnet-v2 --version v2
```

v1、v2 复用 common 中的密钥生成、签名、账户和工厂逻辑。v2 替换验证器，并在部署前向所有验证者探测真实预编译；若所选环境未启用 v2 或仍使用普通客户端，则拒绝部署。`contractVersion=2` 表示新的合约配置，不表示 PQAccount 源码被修改。

新增算法或合约版本时，增加 `v6/` 等版本目录及 profile 模块，并在 `profiles.mjs` 中注册，提供现有适配器中的接口；同时增加相应合约和验收用例。通用的钱包流程与 bench 继续复用。需要不同 EntryPoint 版本时，环境也必须部署匹配版本；当前 one-box 部署的是 v0.7。

每条账户记录绑定 profile、合约版本、创世 hash、EntryPoint、公钥和工厂地址。加载时检查这些标识和确定性地址，不自动把旧账户转换成新算法。合约不可升级；切换 profile 是新建账户，既有账户的迁移需要另行设计。部署时同时检查链上代码和原部署交易，拒绝把新编译产物与旧合约混用。

## 运行数据与边界

所有运行数据位于 `one-box/.runtime/<环境名>/wallet/<profile>/`，由 Git 忽略：

```text
deployment.json        公共部署地址、创建交易、代码 hash
accounts/<id>.json     PQ 私钥与账户记录，仅本地，权限 0600
receipts/              每笔 UserOperation 的交易与 gas
bench-<时间戳>.json    本次顺序 bench 的结果
test-result.json       最近一次完整验收结果
openssl-test/          独立实现生成的公开测试向量；临时私钥测试后删除
```

本地账户文件含明文私钥，目录权限 0700；仅供实验，不是生产钱包的密钥管理方案。没有固定私钥或助记词。`reset` 删除环境时也会删除钱包及其密钥。

当前只支持一笔 UserOperation 执行一个调用；转账接口要求接收地址被动收款，以余额变化核对金额。发送失败保留账户记录和已有收据，不自动再次创建身份。初始资金、gas price 和节点 RPC 来自选定环境，脚本不连接公共链。

当前仅用户账户授权使用 PQ 签名；Bundler 外层交易、BSC 验证者共识和 P2P 身份仍沿用原有机制。

## v3：Falcon-512

```bash
./tools/erc4337-wallet create devnet-v3 --version v3
./tools/erc4337-wallet transfer devnet-v3 <id> <接收地址> 0.01 --version v3
./tools/erc4337-wallet test devnet-v3 --version v3
```

`v3/profile.mjs` 使用 FalconAccountFactory / FalconAccount 和 FalconPrecompile；`native.mjs` 通过 stdin 调用固定 Go/cgo helper，私钥不进入命令行。helper 由 `build --v3` 构建，运行密钥仍只保存在忽略的、0600 权限的账户文件中。版本目录互不覆盖，不迁移或重用 SLH 身份。

v3 公钥使用 896-byte 主体，私钥使用 1,280-byte 主体，原生层恢复固定头。对 32-byte UserOperation hash 签名，签名保留 0x39 头，长度 617–667 bytes。估算使用 667-byte 的非零为主 stub，正式提交发送真实变长签名；估算返回的 sigFailed 不作为正式授权。提交时依然验证真实签名和链上 EntryPoint hash。

v3 的密码学测试包含独立 Bouncy Castle 公共 KAT；账户授权、篡改、重放、转账、Counter 及三验证者一致性检查与 v1/v2 复用 `common/test.mjs` 中的流程。签名使用原生后端，SLH 目前仍用 noble JavaScript；两者端到端时间不是仅算法耗时的公平比较，原生 CPU 比较另见报告。

## v4：ML-DSA-44

```bash
./tools/erc4337-wallet create devnet-v4 --version v4
./tools/erc4337-wallet transfer devnet-v4 <id> <接收地址> 0.01 --version v4
./tools/erc4337-wallet test devnet-v4 --version v4
```

profile 为 `mldsa-v4`，绑定 MLDSAAccountFactory / MLDSAAccount / MLDSAPrecompile。通过 v4 的 Go/cgo helper 调用 mldsa-native；公钥 1,312 bytes、展开私钥 2,560 bytes、签名固定 2,420 bytes。这里保存的是展开私钥，不是可用于派生密钥的 32-byte seed。Pure ML-DSA 对 32-byte UserOperation hash 签名，context 为空，不能与 HashML-DSA 或旧 Dilithium 混用。

估算使用 2,420 bytes 的全非零无效 stub，覆盖真实签名的 calldata 成本上界；正式签名和本地验签后才提交。测试包含原生后端与 Noble 双向互验、独立空 context 公共 KAT，以及共用的授权、篡改、重放、转账、Counter 和三验证者一致性检查。密钥只经 stdin 传入 helper，并保存在忽略的本地账户目录；不写入报告或命令行。

同链比较可以使用 v2/v3/v4 三个 profile，各版本的身份和部署记录独立。切换 profile 不会迁移既有账户。

## v5：ML-DSA-65

```bash
./tools/erc4337-wallet create devnet-v5 --version v5
./tools/erc4337-wallet transfer devnet-v5 <id> <接收地址> 0.01 --version v5
./tools/erc4337-wallet test devnet-v5 --version v5
```

profile 为 `mldsa65-v5`，绑定 MLDSA65AccountFactory / MLDSA65Account / MLDSA65Precompile。公钥 1,952 bytes、展开私钥 4,032 bytes、签名固定 3,309 bytes；对 32-byte UserOperation hash 使用 Pure ML-DSA-65、空 context。估算采用同长度的全非零无效 stub，正式操作经过原生签名和本地验签后提交。

`build --v5` 同时包含 ML44 / ML65 / Falcon 后端；在只构建 v5 的新 checkout 中，旧 profile 可通过 helper 的显式算法选择使用相同旧算法，不能把 ML44 的密钥送入 ML65。旧版本 helper 存在时仍优先使用原文件。测试包含 Noble 双向互验、独立公共 FIPS 204 ML65 向量，以及共用账户授权与三节点一致性检查。所有私钥仍只保存在忽略的运行目录中。
