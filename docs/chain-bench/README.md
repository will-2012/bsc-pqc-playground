# ERC-4337 链路 Bench

2026-10-27 本地实测，profile 为 `slh-dsa-v1`，采用 Solidity 完整验证 SLH-DSA-SHA2-128s，不新增 PQ 预编译。

## 创建账户与转账 gas

三个独立密钥创建三个 PQ 账户，再各转出 0.01 BNB，六笔 UserOperation 全部成功。以下使用外层交易收据的 `gasUsed`：

| 操作 | 样本数 | 平均 gas | 最低 gas | 最高 gas |
| --- | ---: | ---: | ---: | ---: |
| 创建 PQ 账户 | 3 | 6,683,222 gas | 6,502,632 gas | 6,791,680 gas |
| 已部署 PQ 账户转账 | 3 | 5,998,498 gas | 5,736,926 gas | 6,237,303 gas |

平均值按 gas 取整。创建行包含首次 UserOperation 中的 CREATE2 创建账户、SLH 验签和一次向自身的零金额调用；不包含 faucet 充值，也不包含共享验证器和工厂的初始部署。转账行表示已部署账户向被动接收地址发送 0.01 BNB。

[原始结果](slh-dsa-v1.json) 保留逐笔交易 hash、gas、签名大小、操作大小和端到端耗时，不含私钥。本次数据归档自已有本地实验，没有因目录调整重新压测。

计量口径：

- `outerTransactionGas`：外层交易实际 gas，本页表格采用此值。
- `actualGasUsed`：EntryPoint 对 UserOperation 的计费量，包含 `preVerificationGas` 等分摊项，与外层实际 gas 不同。
- `verificationGasLimit`：估算时预留的验证预算，不是实耗。签名中的哈希链步数不同，实际 gas 也会变化。
- 创建耗时 61.56–62.43 s，转账耗时 56.80–61.64 s。包含钱包签名、安全模拟、打包等待和上链；创建还包含生成密钥和充值。这是顺序联通测试，不是算法 CPU 或链吞吐压测。

## 环境与适配

| 组件 | 实测版本 |
| --- | --- |
| 主机 | macOS / Apple M1 Pro |
| BSC | v1.8.1，`947ba1cb…` |
| Alto | 0.0.17，`1d330b71…`，pnpm 8.15.4 |
| EntryPoint | 标准 v0.7 |
| 合约编译 | Solidity 0.8.23、Paris、optimizer runs 1,000,000；Foundry v1.2.1 |
| 钱包 | Node.js v25.9.0，`@noble/post-quantum@0.7.1` |

BSC 和 EntryPoint 均未修改。Alto 保持 `safe-mode`，通过 [PQ 配置](../../one-box/pq-bundler.json) 提高模拟预算，并用 [超时补丁](../../one-box/patches/alto-timeout.patch) 延长 RPC / trace 等待时间。适配说明见 [one-box](../../one-box/README.md#pq-智能账户)。

首次操作编码为 8,576 bytes，已部署账户转账为 8,480 bytes；签名均为 7,856 bytes。这些大小超过 [ERC-7562](https://eips.ethereum.org/EIPS/eip-7562) 公共内存池的 8,192-byte 限制，验证 gas 也超过其 500,000 上限。固定版本的 Alto 实际接受了这些操作，没有修改长度检查源码；不能据此认为符合公共内存池的全部规则。

## 正确性验收

完整验收已通过：

- noble 的三种消息签名，以及 OpenSSL 3.6.3 独立生成的签名，Solidity 均接受；修改消息、公钥、签名或签名长度均拒绝。
- 创建账户、转出 0.01 BNB、调用 Counter 成功。错误签名、篡改转账参数、跨链签名、重复 nonce 和 EOA 越权调用均被拒绝。
- 不匹配 profile 的账户被拒绝；账户私钥文件权限为 0600。
- 三个验证者上的交易区块和 Counter 状态一致，最终性覆盖测试交易。
- Bundler 重启后 PQ 配置保留，原有 ECDSA 全链路回归通过；15 项环境保护测试和合约编译通过。

直接调用验签器的样本交易 gas 估计为 6,183,509 gas；完整测试中的 Counter 外层交易消耗 5,989,090 gas。这两项不属于上表的三账户 bench 样本。

## 复现

[运行步骤](../../tools/chain-bench/README.md) · [钱包用法与版本扩展](../../erc4337-wallet/README.md) · [合约说明](../../contract/README.md)

```bash
./tools/erc4337-wallet test devnet
./tools/chain-bench/run devnet 3 --profile slh-dsa-v1
```

先按运行步骤启动本地环境。完整验收、收据和日志保存在被 Git 忽略的 `one-box/.runtime/<环境名>/`，重置环境会删除这些记录；本目录的 JSON 保留此次公开 bench 数据。

PQ 仅用于用户账户授权；外层交易、共识和 P2P 身份仍沿用原有机制。实验合约未经独立安全审计。
