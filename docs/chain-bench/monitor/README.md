# 分布式压测监控

目的是区分三件事：压力是否真正发出去、链是否稳定处理、瓶颈出现在哪一层。Gas 利用率高不等于系统已饱和，发送目标 QPS 也不等于链上吞吐。

## 数据如何汇总

```text
各目标机器                             中控
Bench / Bundler / Validator
          ↓ 本机采集，每约 2 秒
区块、回执计数、进程及整机资源 → 共享目录 → 每 10 秒汇总
          ↑                               ↓
仅停止指定 Bench 任务        ←       异常停止指令
```

SSM 只负责启动采集器。采集器与 Bench 分开运行，访问配置的本地 RPC，不要求中控直连节点 RPC。共享目录只交换有限窗口的指标与控制信息，不复制账户密钥、签名或交易正文。Bench 停止后仍可观察链恢复。

| 观察对象 | 内容 | 能回答什么 |
| --- | --- | --- |
| 链 | 毫秒级出块间隔 p50 / p95 / 最大值、Gas 利用率、交易数、UserOperation 数、Peers | 是否稳定出块、链上实际处理多少 |
| 共识 | safe / finalized 高度与推进情况、节点高度差、非轮值块、slash 事件 | 压力是否影响最终性或出块 |
| Bench | 目标 QPS、受理与成功回执速率、失败数、按区块归类的回执 | 压力是否送达、回执能否与区块对应 |
| 机器与组件 | 整机 CPU / iowait / steal、可用内存、磁盘余量与读写速率、网络收发；组件 CPU、内存与可用的 I/O 计数 | 是客户端、Bundler、Validator 还是宿主机资源受限 |

整机 CPU 按全部核心归一到 100%，不包含 iowait 和 steal；组件 CPU 的 100% 表示一个核心。systemd 不提供的计数保持未知。当前不采集 Bundler 内部队列和逐笔签名耗时，CPU 指标只能定位到组件。

区块统计与回执对账优先选择覆盖完整、块高最高的节点，不累加三个副本。多个 UserOperation 可以进入同一笔外层交易，因此分别统计、按交易哈希去重。回执确认不等于 finalized；窗口缺失、截断或回执晚到标为不完整，不判定交易丢失。非轮值块不直接等同 backoff 或 slash；slash 事件也不直接表示罚没金额。

## 中控操作

先更新并完成原有 `upgrade` 验收，再使用新入口。以下命令均从仓库根目录执行；配置继续放在私有运行目录。

```bash
# 观察模式，不停止任何压测任务。
./tools/cluster monitor --config "$CONFIG" --interval 10

# 保护指定轮次；在另一个终端启动相同 dataset / run 的 load。
./tools/cluster monitor --config "$CONFIG" --auto-stop \
  --monitor-dataset ml-1000 --monitor-run transfer1
```

自动停止必须绑定 dataset / run，避免旧监控误停新任务。开启后，异常、监控退出或中控心跳超时都会请求停止绑定的 Bench；Validator 和 Bundler 保持运行。`--duration 600` 可限制监控时长，自动停止模式到时也会请求停压。执行升级、配置变更、重启或 reset 前，先结束监控并确认 Bench 状态。

默认预热 30 秒，之后连续 3 个异常窗口触发停压。异常条件包括出块 / safe / finalized 停滞 30 秒、safe 或 finalized 落后超过 64 块、节点落后超过 32 块、观测失败，以及超过 20% 的出块间隔大于 1,350 ms。持续无法读取 slash 日志也属于观测失败。新发生的 slash 或已观测到的 finalized 同高度异哈希直接触发，不等待预热结束。这些是实验保护阈值，可用私有 JSON 文件 `--monitor-policy` 调整；字段见 `one-box/remote/monitor_model.py` 的 `DEFAULTS`。

高 Gas 显示 `HIGH_UTILIZATION`，积压代理指标显示 `BENCH_BACKLOG`，两者本身不触发停压。停止状态区分请求已发出、排空中、已停止和未确认；进程失联或共享存储故障时不能承诺严格实时停止。看到 `DRAINING` / `STOP_UNCONFIRMED` 应继续查询 `load --load-action status`，保留未决交易记录。

中控记录位于 `one-box/.runtime/clusters/<name>/monitor/<session>/`：`latest.json` 是最新快照，`windows.jsonl` 保存每轮证据。共享目录保留节点最新快照；本地采集窗口最多 256 块，交易哈希也有上限。窗口截断会标记，不扫描整个账户池。全部属于私有运行数据，不提交 Git；长时间运行需关注汇总日志的磁盘增长。

## 如何找到稳定上限

1. **先观察空载。** 确认采集完整、最终性正常，记录机器背景负载与采集耗时。
2. **逐级提高总 QPS。** 固定算法、账户池、交易内容、workers 和缓存，每档保留稳定窗口，比较目标、受理、成功回执与实际上链速率。
3. **判断限制位置。** 受理速率跟不上且 Bench CPU 高，先检查客户端；受理增长但回执落后，继续看 Bundler、链 Gas、最终性和 Validator 资源。iowait 高只是存储疑点，需要结合磁盘与进程指标验证。
4. **一次只改一个参数。** 比较 workers、缓存或角色分机前后；同机增加签名并发也可能挤占 Validator。每轮用新 run，保存配置与结果，不自动重试未决交易。

只有提高压力后吞吐不再增长，并出现可重复的排队或资源约束，才有依据讨论饱和点。监控提供证据和停压保护，不自动更改配置，也不将某一次高负载结果当作容量结论。

## 本地验证

```bash
python3 -m unittest discover -s one-box/remote -p 'test_*.py'
node --test tools/chain-bench/load/core.test.mjs
```

测试覆盖历史事件不误停、未知值、重复样本、毫秒时间、回执去重、窗口截断、任务绑定与停止状态。本地三 Validator 实链采集已观察到 450 ms 出块间隔；这不能替代 Linux systemd、SSM、共享目录延迟及真实高负载验收。远程首次使用先运行观察模式，再以小流量验证自动停压。

本次 macOS 本地检查：远程管理及监控 108 项、one-box 19 项、Bench 与 RPC 目标选择 13 项测试通过。ECDSA / ML / SLH 各完成 3 次创建、6 次转账及缓存复用、未决回执恢复检查；回执中的区块与交易哈希已和实链核对。带流量的 4 个有效采集窗口均健康，出块间隔 p50 / p95 / 最大值均为 450 ms。单节点采集耗时约 7–16 ms，只代表本地小样本，不包含 Linux 组件采集及共享存储开销。

口径依据：[BSC RPC](https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/bsc-api-list/)、[ERC-4337](https://eips.ethereum.org/EIPS/eip-4337)；毫秒时间、非轮值块及 slash 事件同时对照仓库锁定的 BSC 源码。
