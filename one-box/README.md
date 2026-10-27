# One-box 本地开发链

配置、内部脚本、验收测试和运行数据集中在此目录。统一入口是仓库根目录下的 `tools/one-box`；以下命令均从仓库根目录执行。

通过一套脚本启动三验证者 BSC 网络。每个新环境自动生成 ECDSA 密钥、BLS 投票密钥、P2P 身份和实验账户，没有内置运行密钥。

当前固定 BSC **v1.8.1**，默认自动激活到 **Jenner**。这是本地实验配置，不代表主网已经激活相同分叉，也不会在每次启动时自动下载上游新版本。

### 准备和启动

需要 Git、Python 3.11 或以上版本、Go（版本要求见 `external/bsc/go.mod`）、Node.js/npm，以及 Foundry 的 `forge`、`cast`。脚本适用于 macOS/Linux，首次构建需要联网下载依赖。

```bash
git submodule update --init --recursive
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

当前仅搭建基础开发链，尚未加入 PQ 预编译、Bundler 或智能账户合约；账户交易、出块、BLS 投票和 P2P 仍使用 BSC 原有机制。

## 验证

```bash
python3 -m unittest discover -s one-box/tests -v   # 配置与 reset 保护检查
python3 one-box/tests/smoke.py                     # 在运行中的 devnet 上检查最终性并转账 0.01 BNB
```

真实网络检查结果分别保存在当前环境的 `health.json` 和 `smoke-result.json`，包含本次区块、最终性高度和交易 hash。

已完成的本机测试和适用边界见[开发链验收记录](docs/devnet-verification.md)。
