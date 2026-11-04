# 分布式账户与转账压测

中控统一分配账户数、操作数和总发送 QPS；每个 Bench 使用配置的 RPC / Bundler。默认同机部署时，连接本机服务。原有 `bench` 成本测试保持不变，新入口为 `load`。

压测期间可另开终端执行 `tools/cluster monitor`，汇总区块、回执和各组件资源，并按指定轮次停压。详见[监控说明](../../../docs/chain-bench/monitor/README.md)。

| 参数 | 账户及链路 |
| --- | --- |
| `ecdsa` | 原生 EOA。准备阶段生成密钥；创建阶段首次充值，使地址进入链上状态；转账直接发普通交易。 |
| `ml` | ML-DSA-44，v4 合约账户，公钥存于 storage。 |
| `slh` | SLH-DSA-SHA2-128s，v7 合约账户，公钥存于 storage、代码复用。 |

PQ 准备阶段生成独立密钥、预测地址并预充值；创建和转账默认走真实 UserOperation，经 Bundler、EntryPoint 和预编译验签。准备阶段的充值与部署成本不计入创建轮次。ECDSA 与 PQ 的创建语义不同，不能把两者直接当作同一种交易的 TPS。

## 中控操作

已有环境使用 `upgrade`。更新原生签名工具或 Bundler 代码时，先 build，再升级；升级保留账户与链数据。

在中控仓库根目录执行：

```bash
git pull --ff-only
CONFIG=one-box/.runtime/cluster-config/ap-pq.json
./tools/cluster build --config "$CONFIG"
./tools/cluster upgrade --config "$CONFIG"
```

发布包检查发生在停止服务之前；构建缺失或过期会直接报错。

当前 upgrade 是整套发布：停止 Bench / Bundler，滚动重启 validator，再启动 Bundler；保留链数据、密钥和账户池。它会补齐已有环境的 Bench 连接配置，并在每台 Bench 上验证三种算法各 1 个账户的准备、创建和转账。其中 SLH 创建使用原实现、转账验证原生签名器。全部成功后才允许正式压测，Bench 服务仍保持停止。若验收失败，会尝试回退程序并保留现场，不能把服务 active 当作链路通过。

新环境仍按远程说明 build、deploy。下面各阶段要等待完成后再执行下一步。

下面示例总计 1,000 个账户，三台 Bench 分到 334 / 333 / 333 个账户；转账总目标 100 QPS，PQ 创建阶段先降速，避免触发 Factory 准入限制：

```bash
CONFIG=one-box/.runtime/cluster-config/ap-pq.json

# 1. 准备账户和资金；等待 status 显示所有客户端 complete。
./tools/cluster load --config "$CONFIG" --load-action prepare \
  --algorithm ml --dataset ml-1000 --run prepare1 \
  --accounts 1000 --qps 100 --workers 32

# 2. 使用上述账户池创建合约；等待完成。
./tools/cluster load --config "$CONFIG" --load-action create \
  --algorithm ml --dataset ml-1000 --run create1 \
  --accounts 1000 --qps 6 --workers 1

# 3. 从相同账户池轮转发出 10,000 笔转账。
./tools/cluster load --config "$CONFIG" --load-action transfer \
  --algorithm ml --dataset ml-1000 --run transfer1 \
  --accounts 1000 --count 10000 --qps 100 --workers 32

# 查看当前任务；可反复执行。
./tools/cluster load --config "$CONFIG" --load-action status

# 停止派发并等待在途请求结束；不停止 validator / Bundler。
./tools/cluster load --config "$CONFIG" --load-action stop
```

改为 `--algorithm ecdsa` 或 `slh`，并使用独立的 `--dataset`，即可运行另外两组。每次新转账轮次用新 `--run`；账户数与原账户池保持一致。`--fund-wei` 默认每账户 0.1 BNB，`--value-wei` 默认每次 1 wei，可按资金与 gas 预算调整。资金不足会停止继续派发，不自动补款。

`--workers` 是**每台客户端**的工作线程上限（1–64）；`--max-inflight` 是每台处理中的操作上限（1–4096，默认等于 workers）。同一线程可以在等待 RPC 或回执时处理其他账户，但同一账户只允许一笔在途操作，避免 nonce 冲突。各客户端使用独立的预充值账户。共享文件系统只用于既有部署分发；压测账户和发送日志应落在目标机本地磁盘。

## 独立控制线程与在途操作

例如，三台客户端总目标 800 QPS，每台 16 个线程、最多 256 个处理中的操作：

```bash
./tools/cluster load --config "$CONFIG" --load-action transfer \
  --algorithm ecdsa --dataset ecdsa-10000 --run "transfer-$(date +%Y%m%d-%H%M%S)" \
  --accounts 10000 --qps 800 --workers 16 --max-inflight 256 --cache-accounts 256 --duration 600
```

ECDSA、ML、SLH 共用调度。每个线程内允许多个不同账户的异步任务；等待回执不会占住该线程唯一的任务位置，CPU 签名由工作线程或其原生签名进程执行。发送许可和充值账户 nonce 由主线程统一分配，回复带独立请求编号，避免并发任务串用结果。

`maxInflight` 的实际上限取参数、该客户端账户数和操作数的最小值，包含构造、签名、等待发送许可及回执的全过程；`peakInflight` 记录实际峰值。未指定新参数时保持原有并发规模。提高在途数会增加内存、RPC 和待确认交易，不能保证达到目标 QPS；SLH 若受签名 CPU 限制，增加在途数也不能替代更多计算能力。到时停止分配及发送许可，已获许可的任务继续完成，密钥和发送前日志仍持久化。

## 按持续时间压测

随机互转可追加 `--recipients-file <私有地址池.json>`。文件为 JSON 地址数组，包含全部已创建发送账户，跨客户端共用相同地址池；每笔等概率选择其他账户，排除自己。默认仍向固定地址转账。地址池只包含地址，不包含私钥；实际收款方和金额记录在每笔私有操作日志中。先完成所有账户的创建并核对地址池，再启动转账。

转账可用 `--duration` 指定秒数。例如，用已有 1 万个 ECDSA 账户，以总目标 100 QPS 运行 10 分钟：

```bash
./tools/cluster load --config "$CONFIG" --load-action transfer \
  --algorithm ecdsa --dataset ecdsa-10000 --run transfer-10m \
  --accounts 10000 --qps 100 --workers 32 --duration 600
```

每个客户端在 worker 就绪、预定启动时间到达后，用单调时钟计时。600 秒不包含启动等待，也不包含最后的回执等待。到时不再派发任务或发放发送许可；未获许可的操作计入 `cancelled`，已获许可的操作继续完成。进程调度、签名与 RPC 有延迟，因此不是承诺最后一笔交易恰好在第 600 秒上链。

- `--duration` 支持创建和转账，范围为 0–86,400 秒；0 表示沿用按数量运行。准备密钥不使用此参数。
- 同时指定 `--count` 与 `--duration` 时，先达到的限制生效。创建仍以账户数为上限，到时可能只创建部分账户。
- 只指定时长的转账，脚本根据 QPS、时长和客户端数生成带舍入余量的数量上限。`total` 是上限，不是必须完成的数量；实际操作数取决于吞吐。
- 正常到时并排空后为 `complete`、`endReason: duration`；按数量完成为 `endReason: count`。`cancelled` 是未发送操作，不是链上失败。出现发送或回执错误仍按原逻辑保留现场。

每轮使用新的 run 名称。`monitor --duration` 限制监控时长，`load --duration` 限制压测派发时长，两者独立。

## 并发与内存复用

`--workers` 控制线程数，`--max-inflight` 独立控制操作并发；`--cache-accounts` 控制每个 worker 最多缓存多少个账户，默认 64、上限 4096，设为 0 可关闭缓存进行对照。按 LRU 淘汰，不会自动把整个账户池读入内存。

缓存复用账户身份、钱包和智能账户对象，省去重复读盘、账户初始化及部分 RPC。nonce、gas 估算、真实签名和回执仍逐笔处理。状态中的 `cacheHits / cacheMisses` 用于判断是否值得扩大缓存；大量新账户只使用一次时，缓存通常没有收益。缓存中的密钥随进程退出释放，JavaScript GC 不保证立即擦除内存。

SLH 的 gas 估算改用 7,856 bytes 的非零占位数据，不再为占位数据生成一次真实签名。EntryPoint 估算允许签名无效；实际发送前仍生成新的 SLH 签名并本地验签。此优化仅在 load Bench 内启用，既有钱包入口保持原行为。

调参时先固定账户池和总操作数，对比 4 / 8 / 16 / 32 workers，再比较缓存关闭与开启。并发增加的是整体吞吐，不保证单笔更快；Bench 与 validator 同机时需保留出块资源。

纯 SLH 签名诊断可在链停止后运行，比较旧的额外占位签名、去掉占位签名，以及 1 / 2 / 4 worker 的吞吐：

```bash
node tools/chain-bench/load/signing-bench.mjs 12
```

该诊断保留真实签名和验签，密钥只在内存中，输出耗时、进程 CPU 与采样 RSS。结果不包含 RPC、Bundler 和链上执行，不能当作链上 TPS。

## 进度与速率

- `dispatched`：开始处理的操作；`attempted`：获得发送许可的请求；`accepted`：RPC 已确认接收。
- `confirmed`：成功回执数（未额外等待最终性）；prepare 阶段表示已准备好的账户数。`failed`：执行失败数；`pending`：运行结束时仍未核定结果的签名请求数；`skipped`：已创建而跳过的账户数。
- `inflight` 包括签名、估算、发送和等待回执。创建 / 转账的 `acceptedPerSecond` 与 `confirmedPerSecond` 按启动工作线程之后的执行时间计算，包含尾部回执等待。
- QPS 是发送上限，**不是保底吞吐**。三个客户端预约同一开始时间，但 SSM、初始化与时钟偏差仍会影响实际起跑；`startLagMs` 记录迟到量。不会在停顿后突发补发。

并发上限会限制吞吐，粗略关系为「所需并发 ≈ 目标 QPS × 单笔端到端时延」。SLH 签名、Bundler 模拟、链上容量、回执等待和本地持久化均可能成为瓶颈。先固定 workers、逐步增加 max-inflight；若签名 CPU 已满，再评估增加 workers；与 validator 同机时还应观察 CPU、磁盘和出块表现。

状态查询会在中控私有目录 `one-box/.runtime/clusters/<环境>/load-results/latest.json` 保存最近结果。目标机的 `runtime/load/<dataset>/runs/<run>/progress.json` 每秒更新；任务结束时立即刷新，避免每笔操作重复刷进度文件；`spec.json`、`operations/` 保存参数与发送回执，服务日志仍为 `logs/bench.log`。

## 密钥与中断

密钥在目标机生成，保存在 `runtime/load/<dataset>/accounts/`；PQ 身份同时沿用钱包的 `runtime/wallet/<profile>/accounts/` 格式。目录权限 700、文件权限 600，使用系统安全随机数。状态查询不上传密钥。这些是明文实验密钥，只用于测试资产；reset 会删除它们，需保留时先私下备份。

发送前持久化签名请求和预期哈希，进程互斥锁避免两个 load 任务争用资金账户。相同 run 不会重发；遇到错误停止新增任务，并等待其他在途操作结束。不要同时手工使用该客户端的资金账户发交易。

超时或强制终止后，用原 dataset、run 和 accounts 核对回执：

```bash
./tools/cluster load --config "$CONFIG" --load-action reconcile \
  --algorithm ml --dataset ml-1000 --run transfer1 --accounts 1000
./tools/cluster load --config "$CONFIG" --load-action status
```

reconcile 不发送交易，只核对并更新结果；原轮次的统计与失败状态保留，核对结果单列在 `reconciliation` 中。仅在旧进程已不存在时清除遗留锁。`runtime/load/unresolved.json` 记录需要核对的任务。未确定的请求会阻止后续发送，需核实链与 Bundler 后再处理，不把“暂时查不到”当成“没有执行”。部分创建完成后，可在核对结束、pending 为 0 时使用新的 create run 补齐，已创建账户会跳过。新 transfer run 则代表一组新的转账，不是失败轮次的自动续跑。

## 本地复现

```bash
node --test tools/chain-bench/load/*.test.mjs
python3 -m unittest discover -s one-box/remote -p 'test_*.py'

# 先构建原生签名器；启动兼容 v4/v7 的环境和 PQ Bundler 后运行：
python3 tools/chain-bench/load/signer/build.py
node tools/chain-bench/load/integration.mjs <环境名>
```

集成测试生成独立账户池，验证三种算法的创建、连续转账、收款余额、公私钥不变、重复 run 拒绝及回执核对；私有原始记录保留在该环境的 `load/` 下。本地测试不替代真实三机的吞吐验收。

限速器每次等待后重新检查单调时钟，不追赶停顿期间积累的额度。目标 QPS 约束发送许可，实际 RPC 提交和上链时间仍受调度、网络与节点处理影响。

### SLH 原生签名与容量验证

默认 `--slh-backend noble` 保留原签名实现；`--slh-backend openssl` 使用常驻的原生签名进程。
算法仍为 Pure SLH-DSA-SHA2-128s、空上下文，沿用原密钥、账户与链上预编译。
每个 worker 对应一个签名进程，复用进程而非每笔重新启动；多个 worker 并行使用 CPU。
每笔生成新签名并本地验签，不缓存签名或省略随机数。密钥只通过匿名管道传递，禁止 core dump。

中控 `cluster build` 会构建并交叉验证原生签名器，随 release 分发，不要求目标机安装新版 OpenSSL。
源码及校验和复用 `tools/crypto-bench/sources.json`；原生签名器独立于 Validator，不改变预编译实现或 gas。
Linux 构建需 `cc`、`make`、`perl`，不强制使用构建机器特有的 CPU 指令。
首次需要编译 OpenSSL；同一源码与编译器的后续构建复用产物。

```bash
# 本地单独构建与交叉验签；支持 macOS arm64、Linux x86_64 / aarch64
python3 tools/chain-bench/load/signer/build.py
# 无链签名容量：1 / 2 / 4 worker，每次使用不同消息并验签
node tools/chain-bench/load/signer/capacity.mjs
# 真实链路：新建独立数据集，核对余额；比较线程数和在途数量
node tools/chain-bench/load/sweep.mjs devnet-v4 ecdsa 32 96
node tools/chain-bench/load/sweep.mjs devnet-v4 ml 32 96
node tools/chain-bench/load/sweep.mjs devnet-v4 slh 16 32
# 可选最后一项：workers:inflight 的比较点（账户池须足够大）
node tools/chain-bench/load/sweep.mjs devnet-v4 ecdsa 256 768 4:64,4:256,8:256
```

远程 SLH 转账在原命令追加 `--slh-backend openssl --workers 4 --max-inflight 32` 即可。
先按物理 CPU 条件逐步增加 worker；过多线程会与同机 Validator、Bundler 争抢 CPU。
原生签名器不可用时直接失败，不静默切换后端。Bench 服务使用 `KillMode=mixed`：先通知主进程停派，
让签名与回执等待完成；超时后 systemd 清理整个进程组。

账户按 worker 固定分组，`--cache-accounts` 仍是每 worker 的 LRU 上限。
覆盖该 worker 的账户数时，后续轮转可以复用账户对象；不会缓存 nonce、回执或签名。
状态里的 `timings` 给出签名、提交、回执等待、限速等待等阶段的次数、总耗时、均值与最大值（ms）。
这是已结束调用的墙钟耗时：并发会重叠，`prepareUserOperation` 包含签名，原生签名时间包含队列等待，不能相加当作 CPU 时间。

目标 QPS 高但接收率低时，先看 `peakInflight` 与 `timings`：回执等待占主导可增加在途上限；
签名占主导且 CPU 高，应增加签名算力或拆分 Bench 与 Validator 的机器。
链上 gas 低、txpool 不持续增长时，不应将客户端吞吐上限解释为链的饱和点。

每客户端超过 1,000 QPS 时，限速器按 `ceil(QPS / 1000)` 笔做小批次，避免逐笔定时器限制吞吐；
批次间按目标速率间隔，停顿后不积累补发额度。准备、创建每个账户只处理一次，转账才轮转复用。
容量脚本先低并发创建 PQ 账户，再测转账；高并发创建可能触发 Bundler 的 Factory 准入限制，
需要单独评估，不能当作 Validator 饱和，也不会自动绕过 Bundler 校验。

Bundler 同时按 gas 与编码大小分包：本地 PQ 路线预留 1 KiB 外层交易开销，避免超过 BSC 的 128 KiB 交易限制。
修正了 Alto 的 AA95 gas 下限计算中重复计入整批 calldata 的问题；calldata 只计一次，实际 gas 估算仍保留。
提高并发后若接收正常、上链停滞，应先检查 Bundler 拒绝原因，不能只继续增加 worker。

本地实测与限制见 [验收报告](../../../docs/chain-bench/load/report.html)。

## 减少普通交易的辅助 RPC

Load 准备普通交易时传入已经核对过的 chainId，避免在 nonce、gas、gasPrice 均已提供时仍调用 `eth_fillTransaction`。
回执只等待原交易哈希，关闭交易替换扫描，避免为寻找替换交易读取整块。
这也适用于 PQ prepare 阶段的普通充值交易；ML / SLH 的 UserOperation 估算、签名和 Bundler 回执路径不变。

前提是 Bench 独占实验账户，不在外部加速或替换其交易。若原交易被替换，等待会超时并保留待核对记录，不把替换交易算作成功。
签名、发送前持久化、成功状态检查与回执核对仍保留。并发和缓存默认值不变，按机器条件调节。

阶梯升压、客户端瓶颈与混部署分析见 [性能排查记录](../../../docs/chain-bench/load/performance.html)。

### 链容量对照（直接提交 EntryPoint）

`--submission direct` 用于隔离链容量：对已创建的 ML / SLH 账户生成真实签名，每笔普通交易提交一个 `handleOps` 操作。保留链上验签、nonce、权限和执行检查；不经过 Bundler 的 gas 估算、ERC-7562 模拟和打包，不能据此报告 Bundler 全链路吞吐。默认仍是 `bundler`，创建流程不变。

先在私有 JSON 中填写经过小流量验证的 `callGasLimit`、`verificationGasLimit`、`preVerificationGas`、`outerGasLimit`，各值使用十进制字符串。外层 gas 必须留出执行余量；不足时测试会失败，不自动扩大或跳过失败操作。

```sh
./tools/cluster load --config "$CONFIG" --load-action transfer \
  --algorithm ml --dataset "$DATASET" --run "$RUN" \
  --accounts 10000 --qps 100 --workers 8 --max-inflight 256 --duration 300 \
  --submission direct --direct-gas-file "$GAS_FILE" --recipients-file "$RECIPIENTS_FILE"
```

发送前持久化 UserOperation 和外层签名交易，外层 nonce 由每台客户端统一分配。成功同时要求外层回执成功及唯一匹配的 `UserOperationEvent`，并核对操作哈希、发送者、nonce 和执行成功标志；恢复只查原交易，不重发。每次测试独占账户池和该客户端的外层发送账户，不与其他创建、充值或转账任务同时运行。

对照结果应单列提交模式、gas 参数、每笔外层交易的操作数、实际成功速率及 CPU。直接提交与 Bundler 多操作分包的摊销不同，不把两者差值全部解释为模拟开销。

### ML 常驻签名进程

`--ml-backend persistent` 每个 Worker 复用一个 v4 密钥工具进程，减少逐笔启动进程的开销；默认 `process` 保持原方式。需要先构建、升级新版密钥工具，旧二进制不支持时直接失败。

每笔仍对实际 UserOperation 重新签名，并在工具内使用公钥验签；不缓存签名或 nonce，不改变链上算法和 gas。请求走匿名管道，禁止 core dump；进程故障会拒绝尚未完成的请求。总排队量受 `--max-inflight` 限制。该选项同时适用于 Bundler 路线和直接提交对照。

构建时执行并发顺序、交叉验签、错钥与进程退出检查；可单独运行 `node one-box/tests/ml_signer.mjs` 复核 v4 工具。高压实验仍需分开观察 Bench 和 Validator 的 CPU，不能用签名器的独立性能推算链吞吐。

### SLH SIMD 签名对照

Linux x86_64 可选择 `--slh-backend avx2`；支持 AVX-512F 的机器还可选 `avx512`。两者使用 [parallel-sphincsplus](https://github.com/sphincs/parallel-sphincsplus) 的不同固定提交，算法均为 Pure SLH-DSA-SHA2-128s、空上下文、7,856 bytes 签名。每个 Worker 启动一个常驻进程，以 SIMD 并行计算独立哈希；每个进程只用一个签名线程，使用系统随机数，并逐笔本地验签。

```bash
python3 tools/chain-bench/load/signer/build.py --backend avx2
node tools/chain-bench/load/signer/capacity.mjs avx2 1,2,4,8
# 支持 AVX-512F 时，使用独立的构建产物和结果
python3 tools/chain-bench/load/signer/build.py --backend avx512
node tools/chain-bench/load/signer/capacity.mjs avx512 1,2,4,8
```

远程 build 在构建机支持相应指令且提供 C++ 编译器时构建对应后端，发布包记录并核对二进制摘要。源提交与归档摘要见 `signer/parallel.json`；构建执行上游已知答案、上下文及跨实现验签测试。配置负载时先检查目标机的工具是否存在、能否启动，失败便停止分发负载，不自动换回其他后端。默认仍为 `noble`；各后端结果分列，对照相同 Worker、在途数和账户池。
