# 分布式账户与转账压测

中控统一分配账户数、操作数和总发送 QPS；每个 Bench 使用配置的 RPC / Bundler。默认同机部署时，连接本机服务。原有 `bench` 成本测试保持不变，新入口为 `load`。

压测期间可另开终端执行 `tools/cluster monitor`，汇总区块、回执和各组件资源，并按指定轮次停压。详见[监控说明](../../../docs/chain-bench/monitor/README.md)。

| 参数 | 账户及链路 |
| --- | --- |
| `ecdsa` | 原生 EOA。准备阶段生成密钥；创建阶段首次充值，使地址进入链上状态；转账直接发普通交易。 |
| `ml` | ML-DSA-44，v4 合约账户，公钥存于 storage。 |
| `slh` | SLH-DSA-SHA2-128s，v7 合约账户，公钥存于 storage、代码复用。 |

PQ 准备阶段生成独立密钥、预测地址并预充值；创建和转账都走真实 UserOperation，经 Bundler、EntryPoint 和预编译验签。准备阶段的充值与部署成本不计入创建轮次。ECDSA 与 PQ 的创建语义不同，不能把两者直接当作同一种交易的 TPS。

## 中控操作

已有环境使用 `upgrade`，不要 reset 或重新创建创世块。本次仅修改钱包、Bench 和管理脚本，没有新增合约或修改 BSC 二进制；中控原来的 Linux 构建缓存有效时，无需重新 build。

代码提交并推送后，在中控仓库根目录执行：

```bash
git pull --ff-only
CONFIG=one-box/.runtime/cluster-config/ap-pq.json
./tools/cluster upgrade --config "$CONFIG"
```

若提示构建缺失或过期，先执行 `./tools/cluster build --config "$CONFIG"`，再重试 upgrade。发布包检查发生在停止服务之前；缺少新脚本会直接报错。

当前 upgrade 是整套发布：停止 Bench / Bundler，滚动重启 validator，再启动 Bundler；保留链数据、密钥和账户池。它会补齐已有环境的 Bench 连接配置，并在每台 Bench 上验证三种算法各 1 个账户的准备、创建和转账。全部成功后才允许正式压测，Bench 服务仍保持停止。若验收失败，会尝试回退程序并保留现场，不能把服务 active 当作链路通过。

新环境仍按远程说明 build、deploy。下面各阶段要等待完成后再执行下一步。

下面示例总计 1,000 个账户、100 QPS，三台 Bench 分到 334 / 333 / 333 个账户，各自目标约 33.33 QPS：

```bash
CONFIG=one-box/.runtime/cluster-config/ap-pq.json

# 1. 准备账户和资金；等待 status 显示所有客户端 complete。
./tools/cluster load --config "$CONFIG" --load-action prepare \
  --algorithm ml --dataset ml-1000 --run prepare1 \
  --accounts 1000 --qps 100 --workers 32

# 2. 使用上述账户池创建合约；等待完成。
./tools/cluster load --config "$CONFIG" --load-action create \
  --algorithm ml --dataset ml-1000 --run create1 \
  --accounts 1000 --qps 100 --workers 32

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

`--workers` 是**每台客户端**的并发上限（1–64）。工作线程并行完成签名与 RPC；同一账户只允许一笔在途操作，避免 nonce 冲突。各客户端使用独立的预充值账户。共享文件系统只用于既有部署分发；压测账户和发送日志应落在目标机本地磁盘。

## 并发与内存复用

`--workers` 控制每台并行操作数；`--cache-accounts` 控制每个 worker 最多缓存多少个账户，默认 64、上限 4096，设为 0 可关闭缓存进行对照。按 LRU 淘汰，不会自动把整个账户池读入内存。

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

并发上限会限制吞吐，粗略关系为「所需并发 ≈ 目标 QPS × 单笔端到端时延」。SLH 签名、Bundler 模拟、链上容量、回执等待和本地持久化均可能成为瓶颈。先逐步增加 workers，再提高 QPS；与 validator 同机时还应观察 CPU、磁盘和出块表现。

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
node --test tools/chain-bench/load/core.test.mjs
python3 -m unittest discover -s one-box/remote -p 'test_*.py'

# 已启动兼容 v4/v7 的环境和 PQ Bundler 后运行：
node tools/chain-bench/load/integration.mjs <环境名>
```

集成测试生成独立账户池，验证三种算法的创建、连续转账、收款余额、公私钥不变、重复 run 拒绝及回执核对；私有原始记录保留在该环境的 `load/` 下。本地测试不替代真实三机的吞吐验收。
