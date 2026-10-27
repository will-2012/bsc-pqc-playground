# 链路 Bench

调用 `erc4337-wallet` 的同一 API，顺序创建独立账户，再各转账 0.01 BNB。算法 CPU 的多方案对比仍在 `tools/crypto-bench/`；这里比较真实客户端预编译和链上交易。

`run` 是公共入口，`common/run.mjs` 是两版共用的链上测量流程，通过 `--version v1/v2` 选择钱包。`v2/` 只放 v2 独有的原生性能测试和向量生成器。v1 没有独有的压测实现，因此不建立空目录或复制脚本。

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
