# One-box 本地开发链

配置、内部脚本、验收测试和运行数据集中在此目录。统一入口是仓库根目录下的 `tools/one-box`；以下命令均从仓库根目录执行。

通过一套脚本启动三验证者 BSC 网络。每个新环境自动生成 ECDSA 密钥、BLS 投票密钥、P2P 身份和实验账户，没有内置运行密钥。

当前固定 BSC **v1.8.1**，默认自动激活到 **Jenner**。这是本地实验配置，不代表主网已经激活相同分叉，也不会在每次启动时自动下载上游新版本。

### 准备和启动

需要 Git、Python 3.11 或以上版本、Go（版本要求见 `external/bsc/go.mod`）、Node.js/npm，以及 Foundry 的 `forge`、`cast`。脚本适用于 macOS/Linux，首次构建需要联网下载依赖。

```bash
git submodule update --init
./tools/one-box doctor
./tools/one-box build
./tools/one-box up
```

`up` 在环境不存在时先初始化，然后等待节点互联、持续出块、最新分叉生效及 BLS 最终性，并提交、检查三笔 StakeHub 注册交易。首次启动通常需要约三分钟；失败时保留日志，便于排查。

默认 RPC：

| 节点 | 地址 |
| --- | --- |
| validator 0 | `http://127.0.0.1:19545` |
| validator 1 | `http://127.0.0.1:19547` |
| validator 2 | `http://127.0.0.1:19549` |

服务只面向本机。chain ID 为 **714**；当前系统合约升级复用 BSC 的 Rialto 开发链代码，因此暂不支持直接修改 chain ID。

### 常用操作

```bash
./tools/one-box status                       # 查看进程和块高
./tools/one-box check                        # 检查节点一致、出块与最终性推进
./tools/one-box accounts                     # 查看预充值账户，不输出私钥
./tools/one-box down                         # 停止，保留密钥和数据库
./tools/one-box up                           # 启动已有环境
./tools/one-box restart                      # 重启全部验证者，保留数据

./tools/one-box config                       # 查看当前环境配置
./tools/one-box config gas_limit 80000000     # 修改出块 gas 目标
./tools/one-box restart                      # 重启后应用配置；gas limit 按协议逐块调整

./tools/one-box build                        # 重新构建当前 submodule 中的客户端代码
./tools/one-box upgrade --binary one-box/.runtime/build/geth

./tools/one-box reset --yes                  # 删除当前环境，生成新密钥并重新启动
```

`upgrade` 更换客户端，保留创世状态和数据，核对启动及最终性后才报告成功。保留上一份二进制；失败时停止节点并恢复旧二进制，**不回滚数据库**。当前只接受与已验证 v1.8.1 分叉配置匹配的构建。升级到新的上游版本时，需要先适配 `one-box/forks.json`、系统合约和启动参数，不能保证任意版本间直接替换。

`reset` 默认沿用该环境的配置，删除并重建全部密钥、余额历史和数据库；也可通过 `--config` 指定新配置。没有 `--yes` 时拒绝执行。需要保留环境时使用 `down` / `restart`。

### 配置与多个环境

公共默认值在 [`config.json`](config.json)，节点模板在 [`templates/node.toml`](templates/node.toml)。初始化后，每个环境使用自己的配置副本。

```bash
# 复制公共配置，修改端口、节点数、初始余额等，再生成独立环境
cp one-box/config.json /tmp/pq-lab.json
# 编辑 /tmp/pq-lab.json；同时运行的环境应使用不同端口
./tools/one-box --name lab init --config /tmp/pq-lab.json
./tools/one-box --name lab up
./tools/one-box --name lab down
```

可直接调整并重启的参数：gas limit、gas price、缓存、RPC/P2P 端口。节点数、初始资金和分叉计划属于建链配置，需要用新的配置文件执行 `reset --yes --config <文件>`，或创建另一环境。原有链的分叉时间不会因为重启而变化。

### 实验账户与初始资金

默认生成三名验证者，每名预置 **1,000,000 BNB**，其中 20,001 BNB 用于注册；另生成五个实验账户，每个预置 **10,000 BNB**。全部是本地链测试资产。

地址、公钥、创世 hash、客户端版本等保存在 `one-box/.runtime/devnet/environment.json`。实验账户的加密 keystore 和独立随机密码位于 `one-box/.runtime/devnet/accounts/test0` 至 `test4`，后续钱包和部署脚本可复用这些文件。

例如使用第一个实验账户转账：

```bash
cast send <接收地址> --value 0.01ether --legacy --gas-price 1gwei \
  --rpc-url http://127.0.0.1:19545 \
  --keystore "$(find one-box/.runtime/devnet/accounts/test0/keystore -type f | head -1)" \
  --password-file one-box/.runtime/devnet/accounts/test0/password.txt
```

### 分叉与系统合约

本地链从创世状态启动，先完成按块高触发的早期分叉。**第一次 `up`** 按当时的时间生成固定计划：60 秒后启用 Shanghai 至 Maxwell 等分叉，90 秒后启用 Fermi、Osaka、Mendel、Pasteur 和 Jenner。计划保存在 `one-box/.runtime/<环境名>/fork-times.json`，以后启动复用同一份。

不能只把所有分叉设为创世时已经激活：BSC 部分系统合约依赖跨越分叉时执行升级。本脚本使用 `--rialtohash` 将生成的创世块识别为本地 Rialto 网络，执行相应升级；检查 Jenner 时还验证 PaymentLane 系统合约已安装。

创世合约固定在 `234e3685…`，作为与原 `node-deploy` 实验一致的基础版本；后续升级字节码由固定的 BSC 客户端提供。生成时仅修改 `one-box/.runtime` 下的副本，补入本次验证者、公钥、实验治理地址和开发参数，不修改 submodule 源码。上游模板里的固定预充值账户全部移除。

## 目录

```text
tools/one-box                外部统一入口
one-box/
├── config.json              默认配置
├── forks.json               已验证的分叉列表
├── templates/               节点配置模板
├── scripts/                 内部环境管理、创世生成脚本
├── tests/                   配置保护、真实转账测试
├── docs/                    验收记录
└── .runtime/                密钥、构建、数据库和日志；不提交
```

当前提供 BSC 开发链、ECDSA 智能账户基线和 SLH-DSA PQ 智能账户。PQ 账户采用 Solidity 验签，不新增 PQ 预编译。出块、BLS 投票和 P2P 仍使用 BSC 原有机制。

## Alto Bundler 与 ERC-4337

```text
生成的实验账户 → 签署 UserOperation → Alto Bundler
                                         ↓ 提交普通交易
                                  BSC 三验证者网络
                                         ↓
                                     EntryPoint
                                         ↓ 验证并执行
                                   SimpleAccount
                                         ↓
                                   转账 / Counter
```

固定 Alto **0.0.17**（submodule 提交 `1d330b71…`）和 EntryPoint **v0.7**，沿用此前已跑通的版本组合。使用的是 submodule 源码构建的 Alto，不是另行安装的 Alto npm 发行包。

额外需要 pnpm；Alto 上游 `packageManager` 固定 pnpm 8.15.4。只需初始化顶层 submodule，无需为这条链路拉取 Alto 的测试用嵌套 submodule。

```bash
./tools/one-box bundler build     # 构建 Alto、安装锁定依赖、编译 contract/
./tools/one-box bundler up        # 必要时启动验证者；部署合约、充值、启动 Alto
./tools/one-box bundler test      # 真实 UserOperation 联通及拒绝测试
./tools/one-box bundler status
./tools/one-box bundler restart   # 仅重启 Bundler
./tools/one-box bundler down      # 仅停止 Bundler
./tools/one-box down              # 先停 Bundler，再停验证者
```

Bundler 默认地址：`http://127.0.0.1:14337`。默认参数在 [`bundler.json`](bundler.json)，首次使用时复制到当前环境的 `bundler/config.json`，节点 RPC 地址从当前环境配置读取。修改该环境的配置后执行 `bundler restart` 应用；也可通过 `bundler restart --port 14338` 修改端口。多个环境同时运行时，为各环境选择不同端口，例如 `./tools/one-box --name lab bundler up --port 14338`。

`restart` 和客户端 `upgrade` 会恢复原先正在运行的 Bundler。`reset` 会停止 Bundler，并删除该环境的部署记录和密钥；新链上再次执行 `bundler up` 即可重新部署。

部署账户使用本环境自动生成的 `test0` keystore；executor、utility 和收款账户另行随机生成。EntryPoint、工厂、Counter 及 Alto 的 v0.7 模拟合约均通过正常链上交易部署。模拟合约采用 Alto 源码内附的字节码，显式配置地址并关闭自动部署，不依赖固定助记词、预签名部署交易或开放未保护交易。

### 构建与适配

构建时将 Alto 的固定 Git 提交解包到 `one-box/.runtime/build/alto/`，应用 [`alto-localhost.patch`](patches/alto-localhost.patch) 和 [`alto-timeout.patch`](patches/alto-timeout.patch)，再按上游锁文件构建。前者将监听地址改为 `127.0.0.1`；后者允许本地配置更长的 RPC 和 trace 超时，供 Solidity PQ 验签使用。`safe-mode` 保持开启，仍执行签名、opcode 和 storage 模拟检查。ESM 包标记沿用上游发布流程；构建记录保存源码提交和两个补丁的 hash。

修改 Alto 源码后，先在 submodule 内提交，再执行 `bundler build`；构建记录保存实际提交和补丁校验值。重建共享 Alto 前需要停止本仓库所有环境中的 Bundler。

### 测试与运行数据

每次 `bundler test` 使用新的工厂 salt，依次验证：

1. 预充值尚未部署的智能账户。
2. Bundler 估算 gas，拒绝错误 owner 的签名。
3. 首次 UserOperation 通过工厂创建账户，并转出 0.01 BNB。
4. 第二次 UserOperation 调用 Counter，确认直接调用者是智能账户。
5. 拒绝重复 nonce，并核对所有验证者的交易区块和合约状态一致。

智能账户自己支付 UserOperation 的费用；Bundler executor 先支付外层交易 gas，再由 EntryPoint 结算。本基线没有 Paymaster。

| 内容 | 路径（相对当前环境目录） |
| --- | --- |
| 合约地址、部署交易和代码校验值 | `bundler/deployment.json` |
| 当前环境的 Bundler 参数 | `bundler/config.json` |
| executor、utility、收款账户私钥 | `bundler/private.json`，仅本地保存 |
| 包含密钥的 Alto 运行配置 | `bundler/alto.json`，仅本地保存 |
| UserOperation 测试结果 | `bundler/result.json` |
| Alto 日志 | `logs/alto.log` |

以上全部位于被 Git 忽略的 `one-box/.runtime/<环境名>/`；公共模板不含私钥。

版本选择、适配原因和实测范围见 [ERC-4337 验收记录](docs/erc4337-verification.md)。

### PQ 智能账户

`./tools/one-box bundler up --pq` 在当前环境合并 [`pq-bundler.json`](pq-bundler.json) 的实验预算；已运行时使用 `bundler restart --pq`。公共默认配置不变，当前环境的配置会持久保存。

该 profile 将验证模拟预算设为 16M gas、估算总预算 35M gas，每 bundle 最多一个 UserOperation，RPC / trace 超时 180 秒。它不关闭安全模拟，不修改 EntryPoint，也不修改 BSC。Alto 0.0.17 的实际行为不代表符合公共内存池所有大小和 gas 限制；这里只验证独立本地 Bundler。

`bundler build` 还会安装锁定的钱包依赖、编译 PQ 合约。创建账户、转账和批量实验使用外部入口 `tools/erc4337-wallet`、`tools/chain-bench/run`，详见 [钱包说明](../erc4337-wallet/README.md) 和 [PQ 验收记录](../docs/chain-bench/README.md)。

## 验证

```bash
python3 -m unittest discover -s one-box/tests -v   # 配置与 reset 保护检查
python3 one-box/tests/smoke.py                     # 在运行中的 devnet 上检查最终性并转账 0.01 BNB
```

真实网络检查结果分别保存在当前环境的 `health.json` 和 `smoke-result.json`，包含本次区块、最终性高度和交易 hash。

已完成的本机测试和适用边界见[开发链验收记录](docs/devnet-verification.md)。
