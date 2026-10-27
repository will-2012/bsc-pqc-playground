# ERC-4337 PQ 钱包

本地实验钱包：随机生成 PQ 密钥，通过 Alto 和 EntryPoint 创建智能账户，再发送 BNB 或调用合约。当前提供 `slh-dsa-v1`：EntryPoint v0.7、PQAccount v1、SLH-DSA-SHA2-128s，使用 Solidity 完整验签，不新增 PQ 预编译。

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

`test` 额外需要支持 SLH-DSA 的 OpenSSL 3.5+，可用 `OPENSSL=/path/to/openssl` 指定。日常创建、转账不依赖 OpenSSL。Node 依赖由钱包自身的 `package-lock.json` 锁定，`bundler build` 会安装。

`--pq` 只修改当前本地环境的 Bundler 预算，保留 `safe-mode`。配置会保存，重启无需重复传入；已有 Bundler 使用 `./tools/one-box bundler restart --pq`。纯 Solidity 验签不符合公共内存池的标准 gas 预算，只用于独立的本地 PQ Bundler。不要同时启动多个钱包命令争用同一个 faucet；bench 已按顺序调用。

账户创建、转账各自是一笔真实 UserOperation，均经钱包签名、Bundler 模拟、外层交易、EntryPoint 授权验证和执行。faucet 的普通转账仅用于提供本地测试资金，不参与 PQ 授权。没有 Paymaster：账户支付 UserOperation 费用，Bundler 垫付外层交易后由 EntryPoint 结算。

## 版本与扩展

入口支持显式指定版本：

```bash
./tools/erc4337-wallet create devnet --profile slh-dsa-v1
```

| 文件 | 职责 |
| --- | --- |
| `wallet.mjs` | 环境校验、本地充值、部署记录、账户保存、准备与提交操作、等待收据 |
| `profiles/slh-dsa-v1.mjs` | 密钥与签名算法、工厂/账户 ABI、调用编码、EntryPoint 版本、合约部署顺序 |
| `profiles/index.mjs` | 显式注册允许的 profile；未知版本直接拒绝 |
| `cli.mjs` | 创建、转账、部署和验收入口 |
| `test.mjs` | 当前 profile 的密码学和全链路验收 |

新增算法或合约版本时，增加一个 profile 模块并注册，提供现有适配器中的接口；同时增加相应合约和验收用例。通用的钱包流程与 bench 继续复用。需要不同 EntryPoint 版本时，环境也必须部署匹配版本；当前 one-box 部署的是 v0.7。

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
