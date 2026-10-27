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

当前提供 BSC 开发链、ECDSA 智能账户基线和 SLH-DSA PQ 智能账户。默认 v1 采用 Solidity 验签；v2 在独立环境显式增加 PQ 预编译。出块、BLS 投票和 P2P 仍使用 BSC 原有机制。

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

构建时将 Alto 的固定 Git 提交解包到 `one-box/.runtime/build/alto/`，应用 [`alto-localhost.patch`](patches/alto-localhost.patch) 、[`alto-timeout.patch`](patches/alto-timeout.patch) 和带环境开关的 [`alto-slh-v2.patch`](patches/alto-slh-v2.patch)，再按上游锁文件构建。前者将监听地址改为 `127.0.0.1`；后者允许本地配置更长的 RPC 和 trace 超时，供 Solidity PQ 验签使用。`safe-mode` 保持开启，仍执行签名、opcode 和 storage 模拟检查。ESM 包标记沿用上游发布流程；构建记录保存源码提交和各补丁的 hash。

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

### v1 PQ 智能账户

`./tools/one-box bundler up --pq` 在当前环境合并 [`pq-bundler.json`](pq-bundler.json) 的实验预算；已运行时使用 `bundler restart --pq`。公共默认配置不变，当前环境的配置会持久保存。

该 profile 将验证模拟预算设为 16M gas、估算总预算 35M gas，RPC / trace 超时 180 秒。它不关闭安全模拟，不修改 EntryPoint，也不修改 BSC。Alto 0.0.17 的实际行为不代表符合公共内存池所有大小和 gas 限制；这里只验证独立本地 Bundler。

`bundler build` 还会安装锁定的钱包依赖、编译 PQ 合约。创建账户、转账和批量实验使用外部入口 `tools/erc4337-wallet`、`tools/chain-bench/run`，详见 [钱包说明](../erc4337-wallet/README.md) 和 [PQ 验收记录](../docs/chain-bench/README.md)。

## 验证

```bash
python3 -m unittest discover -s one-box/tests -v   # 配置与 reset 保护检查
python3 one-box/tests/smoke.py                     # 在运行中的 devnet 上检查最终性并转账 0.01 BNB
```

真实网络检查结果分别保存在当前环境的 `health.json` 和 `smoke-result.json`，包含本次区块、最终性高度和交易 hash。

已完成的本机测试和适用边界见[开发链验收记录](docs/devnet-verification.md)。

## v2 预编译实验环境

v1 的默认客户端、配置、合约和 `slh-dsa-v1` profile 保留。v2 显式构建实验客户端，并使用新的命名环境，避免更换 v1 的二进制、密钥和数据库：

```bash
./tools/one-box bundler build
./tools/one-box build --v2
./tools/one-box --name devnet-v2 init --v2   # 仅首次；自动生成新密钥
./tools/one-box --name devnet-v2 bundler up --port 14347
./tools/erc4337-wallet test devnet-v2 --version v2
./tools/chain-bench/run devnet-v2 3 --version v2 --ecdsa-baseline
./tools/one-box --name devnet-v2 down
```

已有环境跳过 `init`，直接 `bundler up`；端口配置会保留。构建共享 Alto 前应停止所有环境中的 Bundler。

`build --v2` 从固定 BSC submodule 提交解包到 `.runtime/build/v2/bsc/`，应用 `patches/bsc-slh-v2.patch`，复制 `precompile/v2/` 中的代码，并按 `dependency.json` 的固定版本和校验和加载 Go SLH 库；不会改动 submodule 或原 `build/geth`。构建过程执行预编译单元测试及上游标准验签向量，保存二进制、源码和依赖记录。

v2 二进制带 `pqprecompile` 构建标签；仅 chain ID 714、Parlia、Jenner 后启用 `0x0101`，排除 UBT。普通构建仍不启用该扩展。EVM 构建和 RPC `SetPrecompiles` 使用相同的启用条件，公共分叉 map 和预热地址表保持原样。当前实验不支持通过 RPC 自定义覆盖来重定位或禁用此地址。

`init --v2` 在默认配置上增加整数标记 `pq_precompile=1`，并将默认 RPC/P2P 端口各加 10；自定义 `--config` 时使用文件中的端口。此标记属于建链选择，不能通过 `config` 在线切换。重启和 reset 沿用该环境配置。实际运行文件仍在 `.runtime/<环境名>/bin/geth`，更新同类客户端使用现有 `upgrade --binary` 流程。

Alto 根据当前环境标记启用 `alto-slh-v2.patch` 的 stateless 预编译识别，仍保持 `safe-mode`。单笔 gas 测试按顺序提交，实际每笔交易包含一个操作；不需要 v1 的 `--pq` 高预算配置。`--pq` 是 Bundler 预算，`--v2` 是客户端与环境选择，两者含义不同。

本地 gas 价格为固定 60,000 gas。原生性能、输入 ABI、完整交易成本和限制见 [链路报告](../docs/chain-bench/README.md)。所有密钥、构建副本、运行日志及临时测量仍位于被 Git 忽略的 `.runtime/`。

## v3：共同 PQ 分叉与 Falcon

v3 在 `.runtime/build/v3/` 单独构建，从同一固定 BSC 提交和 v2 SLH 扩展开始，再应用 `bsc-pq-v3.patch`；不会覆盖 v1/v2 二进制。`precompile/v3/` 的 Falcon 后端按固定提交及归档 SHA-256 下载，使用 Go/cgo 和 ARMv8 NEON；当前只验证 macOS arm64。

```bash
./tools/one-box build --v3
./tools/one-box --name devnet-v3 init --v3
./tools/one-box --name devnet-v3 bundler up --port 14357
./tools/erc4337-wallet test devnet-v3 --version v3
./tools/one-box --name devnet-v3 down
```

配置标记 `pq_precompile=3` 选择 v3 客户端；`0` / 缺省仍为 v1，`1` 仍为旧 v2。`init --v3` 默认将 RPC/P2P 各加 20，自定义配置则使用文件端口。标记不可在线切换，reset 保留选择。

v3 增加 `pqTime` 硬分叉，包含 Rules、兼容性、顺序检查及 `--override.pq`。首次启动按持久化的 Jenner 时间加 5 s 设置 PQ 时间；仅 chain ID 714 / Parlia / Jenner 后启用，排除 UBT。在同一个时间条件下启用 SLH `0x0101` 和 Falcon `0x0102`；两者都不会在分叉前启用。普通构建禁用这两个实验预编译，EVM 与 RPC 使用同一注册逻辑。

Alto 为 v3 单独启用 `ALTO_LOCAL_PRECOMPILE_V3`，旧 v2 仅有 SLH 标记，v1 均关闭。`safe-mode` 保持开启。预编译价格 SLH 60,000 gas、Falcon 10,000 gas，方法和链上 trace 见[完整报告](../docs/chain-bench/README.md)。

发现旧模板的 `max-bundle-size` / `max-bundle-wait` 不被锁定的 Alto 版本识别，已清理；`max-gas-per-bundle` 才是实际 gas 上限。单笔测试顺序提交，并发测试可能把多个操作放进同一外层交易。

## v4：ML-DSA-44

v4 在独立的 `.runtime/build/v4/` 构建，复用 v3 的源代码准备流程，再添加 `precompile/v4/`。mldsa-native 固定提交与归档校验和与 crypto-bench 一致；构建时从源码生成 ARM64 优化静态库，并执行上游功能测试、KAT、本仓库正负例、gas 边界、分叉与 fuzz 测试。当前整个客户端仍限定 macOS arm64，不能据此宣称支持 Linux Intel。

```bash
./tools/one-box bundler build
./tools/one-box build --v4
./tools/one-box --name devnet-v4 init --v4
./tools/one-box --name devnet-v4 bundler up --port 14367
./tools/erc4337-wallet test devnet-v4 --version v4
./tools/one-box --name devnet-v4 down
```

配置标记 `pq_precompile=4`，默认 RPC/P2P 各加 30。v4 沿用同一个 `pqTime`，同时启用 SLH `0x0101`、Falcon `0x0102`、ML-DSA `0x0103`；没有增加第二个 PQ 硬分叉。v1–v3 的独立二进制、环境和密钥保留。Alto 根据标记仅在 v4 额外识别 `0x0103`，保持 safe-mode；它是本地实验扩展，不是公共 Bundler 的通用支持。

ML-DSA 输入固定 `publicKey[1312] || userOpHash[32] || signature[2420]`，采用 FIPS 204 Pure、空 context。返回 32 bytes 的 0/1，长度或签名不合法返回 0。定价与完整 gas 见[链路报告](../docs/chain-bench/README.md)。

## v5：ML-DSA-65

v5 在 `.runtime/build/v5/` 中构建，复用 v4 的 ML 后端准备流程，同时编译相同固定版本的 ML-DSA-44 / 65 优化库。两个参数集在同一个 Go 原生包中使用各自命名空间，共用 OS 随机数桥接；不会替换旧版本二进制。上游两种参数的功能测试与 KAT 都会执行。

```bash
./tools/one-box bundler build
./tools/one-box build --v5
./tools/one-box --name devnet-v5 init --v5
./tools/one-box --name devnet-v5 bundler up --port 14377
./tools/erc4337-wallet test devnet-v5 --version v5
./tools/one-box --name devnet-v5 down
```

配置标记 `pq_precompile=5`，默认 RPC/P2P 各加 40。沿用同一个 `pqTime`，在 SLH / Falcon / ML-DSA-44 之外启用 ML-DSA-65 `0x0104`；三个旧地址与价格保留。Alto 仅在 v5 环境额外识别该地址，safe-mode 保持开启。

输入固定 5,293 bytes：`publicKey[1952] || userOpHash[32] || signature[3309]`，采用 FIPS 204 Pure、空 context。公钥 / 签名尺寸不正确或验签失败返回 32-byte 的 0；成功返回 1。当前整套客户端仍仅验证 macOS arm64。
