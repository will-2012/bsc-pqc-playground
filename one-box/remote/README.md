# 远程 PQC 环境

在中控机通过 AWS SSM 管理一套独立测试链。默认三台机器，每台各运行一个 validator、Bundler 和 Bench；角色、端口及连接关系均由私有配置指定。分发沿用 node-deploy 的共享目录方式，不操作原环境的服务。

入口为 `tools/cluster`，命令从仓库根目录执行。已在本地验证管理脚本、生成后的 Go 代码及可移植 ML-DSA 后端；Linux 构建、Amazon Linux 2 运行时和真实 SSM 部署仍需在中控机验收。

## 准备

- 中控机：Linux，与目标机同架构；AWS CLI、Python 3.11+（包含 `tarfile.data_filter`）、Git、C 编译器、Make、Go、Node.js 20.19+、npm、pnpm 8.15.4、Foundry。Go 版本按 BSC 的 go.mod。
- 目标机：systemd、SSM Agent、同版本要求的 Python；Bundler / Bench 机器需要可运行的 Node.js 20.19+。SSM 命令需具有管理本环境目录和服务的权限，当前按 root 执行。
- Amazon Linux 2 默认 Python 和 glibc 较旧，不能直接假定官方 Node.js 20 二进制可运行。先准备兼容的运行时，并确保 SSM 的 PATH 能找到它们；脚本不会替换系统运行时。
- 所有机器能读取同一份共享内容；挂载路径可不同，通过 `shared_controller` 和各主机的 `shared` 对应。共享目录含生成的密钥，只允许本实验管理者访问。
- 安全组允许测试节点间的 P2P、RPC 和 Bundler 连接。RPC 仅供隔离测试网络使用，包含解锁的预充值测试账户。

```bash
git clone --recurse-submodules https://github.com/will-2012/bsc-pqc-playground.git
cd bsc-pqc-playground
git submodule update --init
mkdir -p one-box/.runtime/cluster-config
cp one-box/templates/cluster.example.json one-box/.runtime/cluster-config/ap-pq.json
chmod 600 one-box/.runtime/cluster-config/ap-pq.json
```

构建依赖、客户端和合约产物均在中控机生成，首次构建需要联网下载锁定版本的依赖，无需复制 Mac 的缓存、二进制或 `.runtime`。这些命令应在包含本次远程部署代码的已提交版本上运行。

填写真实实例 ID、region、私网地址、共享路径和 `deploy_root`（部署根目录）。仓库内的私有配置必须位于 Git 忽略路径；模板只含占位符。每台机器只列一次，`roles` 可以拆分，`rpc_node` 指定 Bundler 使用哪个 validator，`bundler_node` 指定 Bench 使用哪个 Bundler。首版使用同架构机器，不做交叉编译。

入口会查找可用的 Python 3.11+，也可通过 `BSC_PQC_PYTHON=/path/to/python3.11` 指定中控解释器。此变量仅用于中控。每个 host 可设置 `python`、`node` 为目标机解释器绝对路径（例如 `/path/to/python3.11`）；未设置时使用 SSM PATH 中的 `python3`、`node`。预检、部署和服务使用同一组解释器。

中控与目标机可使用不同的解释器路径。例如，中控独立安装 Python 时：

```bash
export BSC_PQC_PYTHON=/path/to/python3.11
```

AL2023 目标机可使用 `python3.11`、`nodejs22` 软件包，并在私有配置的每个 host 中设置 `"python": "/usr/bin/python3.11"`、`"node": "/usr/bin/node-22"`。不要修改系统 `/usr/bin/python3`。中控仍需单独满足 Node.js 和 pnpm 的构建要求。

已有环境升级到此版本前，须在私有配置中补充 `deploy_root`，填写原环境所在的父目录，不要照抄模板路径。这个字段只声明现有位置，不会移动数据；已记录的根目录不允许通过 `configure` 修改。

## 构建和部署

```bash
./tools/cluster plan --config one-box/.runtime/cluster-config/ap-pq.json
./tools/cluster precheck --config one-box/.runtime/cluster-config/ap-pq.json
./tools/cluster build --config one-box/.runtime/cluster-config/ap-pq.json
./tools/cluster deploy --config one-box/.runtime/cluster-config/ap-pq.json
```

`precheck` 检查 SSM 与共享目录、平台、权限、时钟、磁盘、内存和端口。未启动服务前，无法验证其 TCP 可达性；部署后通过节点互联和实际 RPC 调用验收。`deploy` 会再次执行预检。

部署生成新的创世块、验证者及实验账户密钥。各节点使用相同的分叉时间，先跨过 Jenner，再在 PQTime 同时启用 ML-DSA-44 和 SLH-DSA-SHA2-128s。时间持久化，重启不会重新计算。默认 Jenner 延迟十分钟，早期分叉提前两分钟，安装需在早期分叉前至少 30 秒完成；超过窗口会明确失败，需增加延迟并 reset 后重新部署。

部署验收包括共同最终化高度的区块与状态根、出块及最终性持续推进、实际分叉配置、两种预编译，以及每个 Bench 的 v4 / v7 创建和转账。每个 Bundler 使用独立的 EntryPoint 与执行账户；各机器使用独立预充值账户。Bench 的连接记录与本机 Bundler 部署记录分开保存，支持连接其他机器的 Bundler。随后还会执行新 load 入口的三种算法小样本验收。全部通过后，持续压测仍保持停止。

## 管理和压测

下列命令均追加 `--config one-box/.runtime/cluster-config/ap-pq.json`：

| 命令 | 行为 |
| --- | --- |
| `status` | 查看各角色服务状态 |
| `stop` | 停止本环境 Bench、Bundler 和 validator，保留数据 |
| `start` / `restart` | 启动或重启链，检查链状态后启动 Bundler；Bench 保持停止 |
| `configure` | 应用修改后的 cache、gas 和 bench 参数；不允许隐式修改拓扑、创世块或分叉时间 |
| `upgrade` | 分发已构建的新版本，保留数据和密钥；拒绝隐式更换上游基线或合约产物 |
| `bench` | 在配置的 Bench 机器执行有限轮次创建和转账 |
| `load` | 分布式账户准备、创建、转账与进度；见[压测说明](../../tools/chain-bench/load/README.md) |
| `collect` | 收集白名单中的测试结果至中控私有运行目录 |
| `monitor` | 节点本地采集、中控每 10 秒汇总；可绑定一轮压测自动停压，见[监控说明](../../docs/chain-bench/monitor/README.md) |
| `reset --confirm ap-pq` | 停止并删除远程本环境；中控保留私有备份，下一次 deploy 生成新身份 |

二进制或合约变更前先执行 `build`；仅脚本更新且中控构建缓存仍有效时，可直接 `upgrade`。发布包会校验构建与必要脚本，再停止服务。升级后会在每台 Bench 验收 ECDSA、ML、SLH 的准备、创建和转账，全部通过才标记 ready。升级失败会尝试恢复旧程序，但不回滚数据库；需要查看各节点状态后再恢复运行。配置失败保留现场；修正后重试 `configure`。`stop`、`status`、`reset` 使用已保存的部署清单，编辑配置后仍可管理原环境。

`bench.version` 支持 v4（ML-DSA-44）和 v7（SLH-DSA-SHA2-128s，公钥存于 storage）。每台客户端按 `accounts × rounds` 执行，各机器可同时运行；当前复用逐账户创建、转账流程，属于全链路成本测试，不代表最大 TPS。需要独立账户池、总 QPS 和并发控制时，使用新增的 `load` 入口。

## 隔离与记录

- 远程目录：`<deploy_root>/<name>`；服务统一命名为 `bsc-pqc-<name>-<role>.service`，其中 role 为 `validator`、`bundler` 或 `bench`。
- `runtime` 保存链数据和密钥，`releases` 保存版本，`current` 指向当前版本，`logs` 保存各角色日志；日志与历史版本不会自动删除，长时间运行需关注磁盘空间。
- 中控运行记录在 `one-box/.runtime/clusters/<name>`；错误记录在 `one-box/.runtime/clusters/last-error.log`。SSM 原始输出和机器信息仅写私有运行目录。共享目录中的请求、响应和身份包也属于私有运行数据，不应提交或发布。AWS 凭据由中控机 IAM Role 或 AWS CLI 配置提供，不写入项目配置。
- 不使用 `pkill`、`killall` 或旧环境服务名。操作前核对目录和服务归属；中控与目标机均加环境锁。停止命令会尝试处理所有节点，并报告失败项。
- 发布包只复制 Git 管理的项目源文件，并显式加入构建产物和依赖；未跟踪的本地配置、Alto 开发配置和测试目录不随包分发。
- 新建 Linux 构建仅包含上述两种预编译，沿用本地实验 gas：10,000 / 60,000；没有重新证明其适合真实机器的性能定价。
- 原有 `tools/one-box` 与 v1–v7 构建入口保留。本地未配置 `remote.json` 时仍使用原来的回环地址。

本地回归检查覆盖配置隔离、SSM 失败与取消、停止顺序、重置重试、身份包生成、初始化中断恢复和升级失败处理。真实网络与 Linux 可运行性不由这些测试替代。

离线检查：

```bash
python3 -m unittest discover -s one-box/remote -p 'test_*.py'
python3 -m unittest discover -s one-box/tests -p 'test_*.py'
```

## 查看进度与故障

构建输出主要写入以下私有日志，终端没有输出不代表构建停止：

| 路径（相对仓库根目录） | 内容 |
| --- | --- |
| `one-box/.runtime/build/cluster/build.log` | 密码库、Go 测试与客户端构建 |
| `one-box/.runtime/build/alto-build.log` | Bundler、依赖与合约构建 |
| `one-box/.runtime/clusters/<name>/build.log` | 构建入口其他输出 |
| `one-box/.runtime/clusters/last-error.log` | 中控异常，按时间追加；排查时看最后一段 |

`installed` 表示安装完成；`validator: active` 表示服务已启动，链健康检查仍在后面。链检查通过后才启动 Bundler，再执行账户创建和转账测试；测试失败时先保留现场，不要直接 reset。

SSM 执行结果保存在环境目录的 `ssm/*-result.json`；远端异常保存在共享目录的 `bsc-pqc-<name>/<请求编号>/error.log`。`response.json` 保存已生成的操作结果。排查时核对记录时间和节点，不要把历史错误当成本次故障；不要公开旁边包含配置和管理令牌的 `request.json`。

预检确认端口可绑定，不代表节点间网络已放通。若区块停滞且 Peer 数为 0，先检查监听地址及节点间 P2P TCP 连通性。复用旧环境端口前，必须确认旧服务已停止、端口已释放。

## 升级排障

- 远程钱包仅探测配置给该客户端的 RPC；中控通过 SSM 分别检查所有 Validator。本地 one-box 仍保留多节点验收。同机部署不要求 Bench 能连接其他 Validator 的 RPC；分机部署则须放通配置的 RPC / Bundler 连接。
- 升级输出逐节点安装、Bundler 初始化及三种算法验收阶段。服务 active 不等于升级通过，以最终 `Upgrade verified` 为准。
- 子进程失败的完整输出保存在共享请求目录的私有 `error.log`（0600），不输出到控制台。日志可能包含交易参数，不应提交或全文公开。
- 升级失败会尝试回退程序，不回滚数据库；服务可能保持停止。先用 `status` 查看状态并读取最新错误，确认后用 `start` 恢复 Validator / Bundler。Bench 不自动恢复，也不要用 reset 处理升级失败。

### 交易池趋势

监控按节点读取 `txpool_status`，显示 `pending`（可执行交易）、`queued`（暂不可执行交易）、总量、相邻采样增量及连续增长时长。各节点可能保存同一批交易，不累加为全链交易数；该指标也不包含 Bundler 的 UserOperation 内存池。

默认连续增长至少 60 秒且累计增加至少 100 笔时，标红并报告 `txpool_growing`。继续命中现有 `consecutive` 次数后形成停压条件；只有启用自动停压时才执行停止。可通过监控配置 `txpool_growth_seconds`、`txpool_growth_min` 调整。数量下降、持平、采样缺失或间隔超过两倍采样周期会重新计算趋势。RPC 不可用显示 `?`，不当作空池，也不单独触发停压。持续增长是处理速度落后于流入速度的信号，需结合 Gas、回执速率和节点资源判断瓶颈。

每轮终端输出以空行分隔。标题区分负载状态与健康状态，`receiptTPS` 为当前采样窗口的回执确认速率，`tx/block` 为该窗口平均每块交易数。正常判定用绿色，异常用红色，压测活动用青色，负载未知用黄色；未知数值显示 `?`，没有阈值的资源指标保持原色。输出重定向或设置 `NO_COLOR` 时不写入颜色控制字符。

终端按“负载与健康汇总、链状态表、机器资源表”展示；空载时隐藏无数据的 Bench 速率行。`Pool P/Q` 为 pending/queued；服务列为状态/CPU%，100% 表示一个逻辑核。主机 CPU% 为整机口径，磁盘与网络速率单位为 MiB/s。
