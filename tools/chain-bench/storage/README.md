# 账户存储阶梯实验

比较 ECDSA 普通账户、ML-DSA-44 合约账户、SLH-DSA-SHA2-128s 合约账户在 1 万、10 万、100 万规模下的存储增量。三种方案分别使用新链，三个 validator 均保留完整状态并校验状态根，使用 path + Pebble。工具需要 Python 3.11+；本轮环境为 macOS arm64。

## 测试口径

- ECDSA：独立 secp256k1 密钥对应的 EOA，每个地址收到 1 wei 后进入状态。
- ML / SLH：现有 PQ Factory 部署真实账户合约，每个账户绑定独立公钥，余额同为 1 wei。共享 EntryPoint、Factory、验签器与批处理工具在基线前部署。
- 大规模创建直接调用 Factory，不经过 Bundler。每组 PQ 在基线前额外创建两个账户，通过 Bundler 完成真实 UserOperation 创建和转账；它们不计入阶梯账户数。此结果是普通账户与合约金库的存储对比，不是纯算法差异，也不是百万次 ERC-4337 吞吐测试。规模账户尚未执行 UserOperation，不包含其首次使用后的 EntryPoint nonce、deposit 等状态增量。
- 不复用公钥、不使用随机字节冒充有效公钥、不改合约为代理、不修改创世 alloc 注入账户。密钥由随机主种子经带方案和索引域分隔的 HMAC-SHA512 派生，再执行标准密钥生成；主种子仅保存在被 Git 忽略的运行目录。
- PQ 每批 8 个账户，EOA 每批 256 个，最多 4 笔交易在途。批处理只减少 RPC/交易调度开销，不省略链上创建。每个批次检查成功收据、账户数量及全部地址的哈希，并抽查实际余额和代码。
- OpenSSL 公钥生成使用 crypto-bench 固定版本的静态库，通过 stdin/stdout 常驻进程并行运行；私钥种子不放在命令行。对照 noble 的相同种子公钥检查在 `test_keys.mjs` 中。
- 不裁剪历史、不执行手工数据库删除。每次停机测量均执行客户端 `db compact`，保留压实前与压实后的大小；主表使用压实后分配量，减少不同后台整理时机的干扰。压实可能需要额外临时空间，测量前另查空间，不能中断该步骤。为让分类统计覆盖所有新账户，三组均使用 `trie_dirty_cache_percent=0`（`--cache.gc 0`，不是 archive 模式），禁用 path 写缓冲，并在每阶梯额外出 160 块，使账户写入越过 128 层内存 diff。最后的系统交易差异仍可能留在 journal 中，不能把分类总和解释成精确的最新状态大小。这是存储测量配置，不用于推断生产吞吐。每阶梯停矿、等待三个节点块哈希与状态根一致，再正常停机测量。共享合约、功能样本和创世状态通过各自基线扣除。

## 结果怎么看

`db inspect` 的账户 snapshot、storage snapshot、账户 Trie、storage Trie、代码分项表示逻辑 key+value 大小；原始输出有舍入，JSON 标记为 approxBytes。Ancient 分类是另一种统计口径，不能把整张 inspect 表当成实际压缩磁盘大小。真实落盘使用 `du -sk`。主表展示三个节点分别扣基线后的平均值，表示单节点增量；统计 `geth/chaindata`（包括其 ancient）的压实后增量；JSON 另存整个 datadir 的大小，后者还包含 journal、共识辅助数据和密钥等，不能直接当成纯账户状态。压实前值也保留。Ancient 已包含在 chaindata 内，不能重复求和。

每个节点分别记录；不把其中一个节点乘以三代替三节点实测。报告表是相对基线的增量。日志、钱包输出单独统计，不计入 chaindata。账户、Trie、代码、区块/收据/索引及历史状态应结合原始分类一起看。阶段端点占用不是全程峰值。

## 构建与运行

复用已有 one-box v6 客户端、Alto、合约构建及 crypto-bench OpenSSL 依赖：

```bash
python3 tools/chain-bench/storage/build.py
python3 -m unittest discover -s tools/chain-bench/storage -p 'test_*.py'
node tools/chain-bench/storage/test_keys.mjs
python3 tools/chain-bench/storage/run.py --run local-20261030
```

默认阶段为 `10000,100000,1000000`，算法顺序为 `ecdsa,ml,slh`。可用 `--steps 32,64` 做独立小规模验证；小规模结果不会冒充正式阶梯。每个 run ID 不可复用为不同参数。运行报告在 `docs/chain-bench/storage/<run>/`；私钥、待发交易、批次明细和链数据库在 `one-box/.runtime/`。

中断后用原命令继续：已确认的批次不重复计数，已签名未确认的交易按原哈希重发。保留原主种子与运行目录。默认在一组全部测量成功后删除该实验专属链目录、保留公开测量和种子，再检查剩余空间并创建下一组；`--keep` 可保留链。不会删除其他 one-box 环境。每阶段至少保留 30 GiB 磁盘；下一组开始要求 40 GiB。达到限制停止负载、正常关闭节点，保留检查点。每个下一阶梯还根据已测三节点增量线性外推，并乘 1.5 倍余量，再加 30 GiB 保留量；不足则暂停，不删除已测数据。

## 多台物理节点

本地启停仅封装在 `run.py`；负载与测量可分开执行：

```bash
# 负载机：私有配置指定 RPC、签名 keystore、Factory/EntryPoint/验签器和本机 artifact 路径
node tools/chain-bench/storage/load.mjs /path/to/private/load.json 10000

# 每台节点正常停机后，在该节点运行
python3 tools/chain-bench/storage/measure.py \
  --binary /path/to/geth --datadir /path/to/node \
  --output /path/to/results/10000 --node node0 --accounts 10000
```

`load.json` 可参考本地 `setup.mjs` 生成的文件。负载机只校验连接链的 genesis hash，不管理远端机器；远端启停、空间保护和结果收集需由部署工具执行，不能用负载机剩余空间代替远端磁盘检查。每台物理节点固定客户端二进制、缓存、历史保留参数，分别记录硬件和文件系统；不要与本机三个进程的耗时直接比较。

总量与单账户增量的核查方法、当前速度限制及多机工具改进要求，见[存储核查与负载工具改进](../../../docs/chain-bench/storage/analysis.md)。

## v7 公钥存 storage

显式选择 `--algorithms slh-storage` 使用新增的 v7 Account/Factory；原 `slh` 与默认分组不变。签名算法、预编译和测量步骤复用，详见 [v7 入口](../v7/README.md)。

正式 v7 对比入口（新 run ID）：

```bash
python3 tools/chain-bench/storage/run.py --run v7-20261031 --algorithms slh-storage --reference-run local-20261030
```

`--reference-run` 自动核对客户端二进制、节点配置、平台与历史策略，每个阶梯生成 `comparison.html/json`。只读取已完成阶段，每节点先扣基线；三节点总量除以 3 和账户数得到每节点平均 bytes/account，并核对总量与单账户放大倍数一致。ECDSA 的代码增量为零时不计算代码项倍数。

独立重建对比，不启动链：

```bash
python3 tools/chain-bench/storage/compare.py --reference docs/chain-bench/storage/local-20261030 --candidate docs/chain-bench/storage/v7-20261031 --output docs/chain-bench/storage/v7-20261031/comparison.html
```

主报告可用同目录的 `report-sources.json` 选择替代分组来源；会核对配置、客户端与阶段一致。此配置仅改变展示，不能改写原始运行 manifest 或样本。
