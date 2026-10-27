# 合约

这里存放合约源码、依赖锁文件和编译配置。部署、钱包和联通测试使用 `one-box/scripts/erc4337.mjs`，统一从仓库根目录的 `tools/one-box` 调用。

| 合约 | 来源与用途 |
| --- | --- |
| EntryPoint v0.7 | `@account-abstraction/contracts@0.7.0`；处理账户创建、验证、nonce、费用及执行 |
| SimpleAccountFactory / SimpleAccount | 同一上游包的标准示例；本阶段使用 ECDSA owner，作为 ERC-4337 基线 |
| Counter | 本仓库的测试合约，记录调用次数和直接调用者 |

`src/Imports.sol` 引入上游实现，不修改 EntryPoint 和账户的授权逻辑。固定 Solidity 0.8.23、Paris 指令集和 OpenZeppelin 5.0.2，依赖由 `package-lock.json` 锁定。

```bash
# 从仓库根目录执行；同时构建 Alto、安装依赖、编译合约
./tools/one-box bundler build

# 启动开发链，部署合约，启动 Bundler
./tools/one-box bundler up
./tools/one-box bundler test
```

合约使用生成的实验账户正常部署，地址随环境变化，不要求以太坊公共网络的标准部署地址。地址和交易记录保存在 `one-box/.runtime/<环境名>/bundler/deployment.json`；这里不存放私钥或运行时部署配置。
