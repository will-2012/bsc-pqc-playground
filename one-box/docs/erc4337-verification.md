# ERC-4337 基线验收

2026-10-27，在 macOS arm64、本仓库的三验证者 BSC v1.8.1 开发链上实测。此阶段使用 **ECDSA 智能账户**，没有实现 PQ 授权。

## 固定组件

| 组件 | 版本与来源 |
| --- | --- |
| Alto | submodule 标签 `@pimlico/alto@0.0.17`，提交 `1d330b710455bf3178b5f3037f0da1566c4970ea`，从源码构建 |
| EntryPoint / SimpleAccountFactory / SimpleAccount | `@account-abstraction/contracts@0.7.0`，保留上游实现 |
| OpenZeppelin | 5.0.2 |
| Solidity / EVM 目标 | 0.8.23 / Paris |
| 钱包适配 | viem 2.44.4、permissionless 0.3.4；ethers 6.13.2 读取生成的 keystore |

上游参考：[Alto 固定源码](https://github.com/pimlicolabs/alto/tree/1d330b710455bf3178b5f3037f0da1566c4970ea)、[标准账户抽象实现](https://github.com/eth-infinitism/account-abstraction/tree/releases/v0.7)。

## 适配与取舍

- Alto 源码解包到被忽略的运行目录构建；唯一行为补丁是绑定 `127.0.0.1`，submodule 保持干净。ESM 包标记与上游发布流程一致。
- EntryPoint、工厂、Counter 和 Alto 的 v0.7 模拟合约通过普通链上部署交易创建，地址显式传给钱包与 Bundler。
- 模拟合约字节码来自固定 Alto 源码；关闭 Alto 的自动部署，以免依赖固定的部署器地址和预签名未保护交易。
- 使用 BSC legacy 外层交易；Alto `safe-mode=true`，保留模拟验证。未通过关闭验证来绕过错误。
- 部署者来自开发链生成的实验 keystore；executor、utility 和收款账户另行随机生成。密钥及包含密钥的配置只写入 `.runtime`。
- 使用自付费智能账户，没有 Paymaster；验证者只接收 Bundler 提交的普通交易，UserOperation 由 EntryPoint 执行。
- 重启测试发现节点刚连上时，一次快照可能赶上 peer 重连或区块传播；健康检查会重试一致快照，并要求所有节点的块高和最终性继续推进。增加了对应回归测试，没有放宽链一致性条件。

## 实测覆盖

| 检查 | 结果 |
| --- | --- |
| 固定源码构建 Alto | 通过，上游锁文件冻结安装 |
| 四类合约部署 | 成功，保存交易与运行时代码 hash；重复部署命令复用原地址 |
| Bundler RPC | 正确返回本环境实际部署的 EntryPoint |
| gas 估算 | `prepareUserOperation` 从 Bundler 获得有效估算 |
| 首次 UserOperation | 创建尚未部署的智能账户，并成功转出 0.01 BNB |
| 合约调用 | 第二次 UserOperation 调用 Counter；调用者为智能账户，计数增加 1 |
| 错误授权、重放 | 错误 owner 的签名、重复 nonce 均拒绝 |
| 验证者一致性 | 所有节点的交易区块、Counter 计数和调用者一致 |
| 完整环境重启 | Bundler 随验证者停止、恢复；密钥、合约地址、创世状态、分叉计划和历史交易保留 |
| 重启后再次操作 | 使用新 salt 创建另一个账户，再次完成转账和合约调用 |
| Bundler 换端口 | 改为 14338 后测试通过；环境配置独立保存，可恢复默认端口 |
| 从关闭状态启动 | `bundler up` 自动拉起验证者并接回原有部署，完成测试后可由 `down` 一并关闭 |
| 重复联通测试 | 5 个新智能账户，完成 10 次成功的 UserOperation（5 次创建并转账、5 次调用 Counter） |
| 自动检查 | 14 项测试通过，包括私钥排除、端口配置、PID 归属与启动快照重试 |

每次测试的新账户 salt 递增，因此可重复运行而不依赖某个固定账户已经部署。首次创建的总 gas 还受工厂首次使用等因素影响，本次不据此给出性能结论。

## 复现

从仓库根目录执行：

```bash
git submodule update --init
./tools/one-box bundler build
./tools/one-box bundler up
./tools/one-box bundler test
./tools/one-box down
```

实际地址与证据保存在 `one-box/.runtime/<环境名>/bundler/deployment.json`、`result.json` 和 `logs/alto.log`。这些是本地运行数据，不纳入 Git；reset 后重新部署。
