# 链路 Bench

调用 `erc4337-wallet` 的同一 API 创建账户、转账，比较实际外层 gas。算法本机性能另见 [crypto-bench](../crypto-bench/README.md)。[HTML 报告](../../docs/chain-bench/report.html)包含最新同链对照，旧版本原始数据保留在 `docs/chain-bench/v*/`。

## 版本

| 版本 | 账户验签 | 钱包 profile |
| --- | --- | --- |
| v1，默认 | SLH-DSA-SHA2-128s，Solidity | slh-dsa-v1 |
| v2 | SLH-DSA-SHA2-128s，预编译 | slh-dsa-v2 |
| v3 | Falcon-512，预编译 | falcon-v3 |
| v4 | ML-DSA-44，预编译 | mldsa-v4 |
| v5 | ML-DSA-65，预编译 | mldsa65-v5 |
| v6 | SLH-DSA-SHA2-128f，预编译 | slh-dsa-v6 |

`run` 和 `common/run.mjs` 是共用链上测量入口。CPU、负载、trace 和分叉检查也在 `common/`；`v*/` 只保留版本参数、入口和必要的向量生成脚本。v1 没有独立实现，因此不建立空目录。

## v6 同链实验

当前完整客户端仅验证 macOS arm64。构建、运行数据与密钥均位于 Git 忽略的 `one-box/.runtime/`。环境配置见 [one-box](../../one-box/README.md)。

```bash
./tools/one-box bundler build
./tools/one-box build --v6

# CPU 测量前，关闭所有本地节点和 Bundler
python3 tools/chain-bench/v6/precompile.py
./tools/one-box --name devnet-v6 init --v6
```

首次启动前，在另一终端运行分叉观察；已有环境经过分叉后无法重现“分叉前”状态：

```bash
python3 tools/chain-bench/v6/fork-check.py devnet-v6
```

主终端启动并验收：

```bash
./tools/one-box --name devnet-v6 bundler up --port 14387
./tools/erc4337-wallet test devnet-v6 --version v6
./tools/chain-bench/run devnet-v6 3 --version v6 --ecdsa-baseline
./tools/chain-bench/run devnet-v6 3 --version v2
./tools/chain-bench/run devnet-v6 3 --version v3
./tools/chain-bench/run devnet-v6 3 --version v4
./tools/chain-bench/run devnet-v6 3 --version v5
node tools/chain-bench/v6/load.mjs devnet-v6 8 5
node tools/chain-bench/v6/trace.mjs devnet-v6
./tools/one-box --name devnet-v6 check
./tools/one-box --name devnet-v6 down
```

v6 沿用共同 `pqTime`，同时启用五个预编译。Alto 保持 safe-mode，默认打包 gas 上限为 20M。八个 128f 操作会超过 BSC 的 128 KiB 单笔交易限制，因此 v6 按真实 calldata 大小拆包。报告以唯一外层交易统计 gas，不假设一轮只有一个 bundle。

旧版本仍可独立运行：把构建和初始化参数改为 `--v2` 至 `--v5`，环境名改为对应版本。Bundler 端口依次为 v2 14347、v3 14357、v4 14367、v5 14377。原生测量、负载、trace 与分叉观察使用相应 `v*/` 入口；钱包只能选择该客户端已包含的算法。

## v1 Solidity 路径

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq
./tools/chain-bench/run devnet 3 --version v1
./tools/one-box down
```

## 数据口径

`run` 的第二个位置参数是账户数量，范围 1–100，默认 3。每个账户随机生成独立密钥，创建和转账各发送一个真实 UserOperation；`--ecdsa-baseline` 另外发送同样数量的原生 EOA 转账，确认每笔为 21,000 gas。顺序测试每笔外层交易一个操作，创建 gas 不含 faucet 充值与共享合约部署。

结果位于 `.runtime/<环境名>/wallet/<profile>/bench-<时间戳>.json`，保存交易 hash、外层实际 gas、EntryPoint 账户计费、gas price、端到端耗时和 calldata floor。私钥不进入报告。

原生测量使用实际客户端 Run，五轮轮转，以进程 CPU 为主指标，记录线程 CPU、墙钟和分配。Go 单执行 P，正常启用 GC；按 ECRECOVER 的 3,000 gas 加余量推算实验价格，不自动修改客户端常量。`native-bench.json` 和原始文本位于 `.runtime/build/v*/`。

负载脚本的两个数字是账户数和轮数，默认 8 / 5。每轮先准备，再并发提交不同 sender；记录准备、提交与收据等待。v3 对比 v2/v3，v4 对比 v3/v4，v5 对比 v4/v5，v6 对比 v2/v6。这些是有限负载测试，不能作为吞吐上限。

## 报告与交叉复核

```bash
python3 tools/chain-bench/report.py
python3 tools/chain-bench/audit.py
```

正文来自 `docs/chain-bench/README.md`，摘要卡片由归档 JSON 计算；更新正文后重新生成 HTML。交叉复核会离线检查两份报告、原始统计、gas、定价、交易去重与公开数据，不启动链，也不改写测量时的源码记录。
