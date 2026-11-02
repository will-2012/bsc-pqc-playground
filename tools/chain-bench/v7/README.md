# v7：SLH 公钥存储对照

算法与 v2 同为 SLH-DSA-SHA2-128s，复用同一验签预编译及 Gas 定价。仅新增账户和工厂：公钥由 immutable 改为一个 storage 槽。钱包 profile 为 `slh-dsa-v7`，部署和身份文件独立，不覆盖旧版本。

## 创建与转账

使用已启用 v2 预编译的环境，例如现有 v6 客户端环境。无需新增 `--v7` 客户端参数。

```bash
./tools/one-box --name devnet-v6 bundler up --port 14387
./tools/erc4337-wallet create devnet-v6 --version v7
./tools/erc4337-wallet transfer devnet-v6 <id> <收款地址> 0.01 --version v7
./tools/chain-bench/run devnet-v6 3 --version v7 --ecdsa-baseline
```

## 存储压测

新增 `slh-storage` 分组；原 `slh` 仍对应 v2 immutable，原默认三组不变。使用独立 run ID：

```bash
# 小规模验证：含两个真实 UserOperation 创建/转账样本
python3 tools/chain-bench/storage/run.py --run v7-smoke --algorithms slh-storage --steps 16,32

# 百万规模，明确需要时再运行
python3 tools/chain-bench/storage/run.py --run slh-storage-million --algorithms slh-storage
```

保留原实验的独立有效密钥、每账户 1 wei、真实 Factory、三节点及停机测量口径。公钥生成沿用 SLH 实现；存储组有独立运行目录和种子。对照时分别看代码、storage snapshot、storage Trie 和实际落盘，不能只看 storage 判断收益。

验证：`forge test --root contract --match-contract PQStorageAccountTest -vv`。覆盖公钥在 slot 0、不同公钥共享代码、CREATE2 地址、重复创建、非 EntryPoint 调用拒绝及无改钥入口，并对照原 immutable 账户仍产生不同代码。

## 已完成验证

2026-10-31，本地三节点 v6 客户端上完成两组真实 UserOperation 创建和转账，以及 16 → 32 账户存储阶梯。三个节点账户 snapshot 增量均为 32，代码记录增量均为 0（共享代码在基线前已部署）。链上抽查两个不同公钥账户：公钥与 slot 0 一致，运行时代码均为 1,778 bytes 且完全相同。

两笔创建的外层交易分别为 755,214 / 755,082 gas，转账为 344,580 / 344,310 gas；仅是小样本，不代表与旧版同链对照后的性能结论。完整结果见 [验证报告](../../../docs/chain-bench/storage/v7-smoke/report.html) 和 [链上代码/槽位检查](../../../docs/chain-bench/storage/v7-smoke/v7-check.json)。实验节点已关闭。另已完成 1 万、10 万、100 万正式阶梯，见[存储对比](../../../docs/chain-bench/storage/v7-20261031/comparison.html)；小规模样本与正式结果分别保存。
