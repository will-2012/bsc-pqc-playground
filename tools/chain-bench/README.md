# 链路 Bench

调用 `erc4337-wallet` 的同一 API，顺序创建独立账户，再各转账 0.01 BNB。算法 CPU 的多方案对比仍在 `tools/crypto-bench/`；这里比较真实客户端预编译和链上交易。

`run` 是公共入口，`common/run.mjs` 是各版共用的链上测量流程，通过 `--version v1/v2/v3/v4/v5` 选择钱包。`v2/` 只放 v2 独有的原生性能测试和向量生成器。v1 没有独有的压测实现，因此不建立空目录或复制脚本。

## v1：Solidity

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq
./tools/chain-bench/run devnet 3 --version v1
./tools/one-box down
```

## v2：预编译与 EOA 基线

先按 [one-box](../../one-box/README.md#v2-预编译实验环境) 构建并初始化 v2 环境：

```bash
./tools/one-box --name devnet-v2 bundler up --port 14347
./tools/chain-bench/run devnet-v2 3 --version v2 --ecdsa-baseline
./tools/one-box --name devnet-v2 down
```

第二个参数为账户数量，范围 1–100，默认 3。版本默认仍为 v1；每个账户随机生成独立密钥，创建和转账各发送一笔真实 UserOperation。`--ecdsa-baseline` 额外发送同样数量的原生 EOA 转账，逐笔确认消耗 21,000 gas。

结果写入 `one-box/.runtime/<环境名>/wallet/<profile>/bench-<时间戳>.json`。保存交易 hash、外层实际 gas、账户计费、外层 gas price、端到端耗时及真实 calldata floor。创建 gas 不包含独立的 faucet 充值或共享合约部署。密钥不进入报告。

## 原生预编译性能

先构建 v2，停止本地链，再执行：

```bash
./tools/one-box build --v2
python3 tools/chain-bench/v2/precompile.py
```

脚本拒绝在本地链仍运行时测量。使用实际 BSC `Run` 实现、相同消息、五轮轮转、Go 单执行 P 和正常 GC，同时记录进程 CPU、线程 CPU、墙钟与分配；以 ECRECOVER 的 3,000 gas 推导实验报价。测量不自动更改客户端定价常量。结果在 `.runtime/build/v2/native-bench.json`，原始输出为 `native-bench.txt`。

预编译代码和公开向量在 `one-box/precompile/v2/`，依赖固定。需要重新生成公开向量时可运行 `node tools/chain-bench/v2/vectors.mjs`，需要钱包依赖和 OpenSSL 3.5+；临时私钥仅在忽略目录中使用并删除。改动向量后应重新构建、测试及测量，不与旧结果混用。

当前归档结果及口径见 [docs/chain-bench](../../docs/chain-bench/README.md)。

## HTML 报告

```bash
python3 tools/chain-bench/report.py
```

从 `docs/chain-bench/README.md` 和已归档的原始 JSON 生成 [report.html](../../docs/chain-bench/report.html)，仅使用 Python 标准库。页面可离线查看，支持窄屏表格滚动和打印；文档更新后重新生成，避免两份内容分别维护。

## v3：Falcon-512 与同链对照

当前仅 macOS arm64：

```bash
./tools/one-box bundler build
./tools/one-box build --v3
# 原生测量必须在所有本地链和 Bundler 停止时执行
python3 tools/chain-bench/v3/precompile.py
./tools/one-box --name devnet-v3 init --v3  # 首次执行；已有环境跳过
./tools/one-box --name devnet-v3 bundler up --port 14357
./tools/erc4337-wallet test devnet-v3 --version v3
./tools/chain-bench/run devnet-v3 3 --version v3 --ecdsa-baseline
./tools/chain-bench/run devnet-v3 3 --version v2
node tools/chain-bench/v3/load.mjs devnet-v3 8 5
node tools/chain-bench/v3/trace.mjs devnet-v3
./tools/one-box --name devnet-v3 check
./tools/one-box --name devnet-v3 down
```

首次 `bundler up` 前，可在另一个终端先启动 `python3 tools/chain-bench/v3/fork-check.py devnet-v3`，观察三个节点在 PQ 分叉前后同时关闭 / 启用两个预编译。已经过分叉的环境无法重现“之前”的状态，需要新的命名环境。

v2 / v3 原生性能入口共用 `common/native_bench.py`，统一五轮、CPU 口径和定价公式；v3 增加 Falcon 验签正负例及原生签名路径。结果在 `.runtime/build/v3/native-bench.json`，不会自动更改客户端价格。

负载脚本先测同链 SLH v2，再测 Falcon v3。第二、三个参数为账户数和轮数；默认 8 / 5。不同账户在每轮并发提交，按唯一外层交易统计 gas。账户准备不计入负载窗口；签名、估算和收据等待分别记录。结果位于 `.runtime/<环境>/wallet/load-v2-v3.json`；不是最大 TPS 测试。

`max-bundle-size`、`max-bundle-wait` 不是当前 Alto 0.0.17 的有效选项，已从模板和加载逻辑中清理；单笔 gas 测试依靠顺序提交，不依靠包内数量限制。v3 的默认实际打包 gas 上限为 Alto 的 20M gas。

## v4：ML-DSA-44

```bash
./tools/one-box bundler build
./tools/one-box build --v4
# 先停止所有本地链和 Bundler，再测五轮原生 CPU
python3 tools/chain-bench/v4/precompile.py
./tools/one-box --name devnet-v4 init --v4
# 首次启动前可在另一终端运行：
# python3 tools/chain-bench/v4/fork-check.py devnet-v4
./tools/one-box --name devnet-v4 bundler up --port 14367
./tools/erc4337-wallet test devnet-v4 --version v4
./tools/chain-bench/run devnet-v4 3 --version v4 --ecdsa-baseline
./tools/chain-bench/run devnet-v4 3 --version v3
./tools/chain-bench/run devnet-v4 3 --version v2
node tools/chain-bench/v4/load.mjs devnet-v4 8 5
node tools/chain-bench/v4/trace.mjs devnet-v4
./tools/one-box --name devnet-v4 check
./tools/one-box --name devnet-v4 down
```

v4 的定价测试继续使用 `common/native_bench.py`，增加 ML-DSA 正负例与签名，五轮共 70 组测量。负载测试比较同链 Falcon v3 与 ML-DSA v4；两者都使用 Go/cgo helper，分别记录准备、提交和收据时间。每轮不同 sender 并发，仍是有限负载测试，不代表吞吐上限。trace 校验三个预编译的实际调用 gas。

公开结果归档在 `docs/chain-bench/v4/`，旧版本结果不覆盖。运行数据、密钥、构建产物仍全部位于 `.runtime/`。

## v5：ML-DSA-65 与 ML-DSA-44 同链对照

```bash
./tools/one-box bundler build
./tools/one-box build --v5
# 所有本地节点与 Bundler 关闭时测量五轮原生 CPU
python3 tools/chain-bench/v5/precompile.py
./tools/one-box --name devnet-v5 init --v5
# 首次启动前，可在另一个终端观察共同分叉：
# python3 tools/chain-bench/v5/fork-check.py devnet-v5
./tools/one-box --name devnet-v5 bundler up --port 14377
./tools/erc4337-wallet test devnet-v5 --version v5
./tools/chain-bench/run devnet-v5 3 --version v5 --ecdsa-baseline
./tools/chain-bench/run devnet-v5 3 --version v4
./tools/chain-bench/run devnet-v5 3 --version v3
./tools/chain-bench/run devnet-v5 3 --version v2
node tools/chain-bench/v5/load.mjs devnet-v5 8 5
node tools/chain-bench/v5/trace.mjs devnet-v5
./tools/one-box --name devnet-v5 check
./tools/one-box --name devnet-v5 down
```

v5 继续复用 common 中的 CPU、gas、负载、trace 与分叉检查实现。原生测试包含 ECRECOVER、SLH、Falcon、ML44、ML65 的有效 / 无效路径与原生签名，五轮共 95 组；定价仍以 ECRECOVER 的 3,000 gas 为基准。并发测试对比同链 ML44 / ML65，默认每版 8 个账户 × 5 轮，属于有限负载测试。

结果归档在 `docs/chain-bench/v5/`。所有旧归档保留，私钥与运行文件不进入 docs。
