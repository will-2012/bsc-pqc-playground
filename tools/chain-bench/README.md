# 链路 Bench

调用 `erc4337-wallet` 的公共 API，顺序创建 PQ 智能账户，再各转账 0.01 BNB。这里存放链路测试脚本；算法 CPU 测试仍在 `tools/crypto-bench/`。

从仓库根目录执行：

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq
./tools/chain-bench/run devnet 3 --profile slh-dsa-v1
./tools/one-box down
```

第二个参数是账户数量，范围 1–100；默认 3。每个账户使用独立随机密钥，创建和转账各发送一笔真实 UserOperation。脚本复用钱包的 profile，不实现自己的签名或合约调用逻辑。

结果写入 `one-box/.runtime/<环境名>/wallet/<profile>/bench-<时间戳>.json`，包含交易 hash、实际外层 gas、UserOperation 计费量和端到端耗时。运行密钥和日志留在被忽略的运行目录。

已记录的结果见 [docs/chain-bench](../../docs/chain-bench/README.md)。
