# BSC PQC Playground

本仓库用于在 BSC 上开展抗量子密码学（PQC）相关实验。

- `external/`：固定版本的 BSC、创世合约和 Alto 源码，通过 Git submodule 管理。
- `contract/`：ERC-4337 相关合约源码、依赖和编译配置。
- `erc4337-wallet/`：按版本适配的 PQ 钱包，提供创建智能账户、转账和测试接口。
- `one-box/`：本地开发链的配置模板、内部脚本、测试、文档和运行数据。
- `tools/one-box`：开发链的统一入口。
- `tools/chain-bench/`：调用钱包接口的链路测试；结果归档在 `docs/chain-bench/`。

BSC 和 Alto 分别使用 [will-2012/bsc](https://github.com/will-2012/bsc) 和 [will-2012/alto](https://github.com/will-2012/alto) fork，便于维护实验改动；submodule 仍锁定具体 commit。

```bash
git submodule update --init
./tools/one-box doctor
./tools/one-box build
./tools/one-box up
```

使用方式、初始资金和配置说明见 [one-box 文档](one-box/README.md)。

运行 ERC-4337 基线：

```bash
./tools/one-box bundler build
./tools/one-box bundler up
./tools/one-box bundler test
./tools/one-box down
```

合约说明见 [contract 文档](contract/README.md)。ECDSA 基线与 SLH-DSA PQ 智能账户链路均保留。

运行 PQ 全链路（Solidity 验签，无新增 PQ 预编译）：

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq
./tools/erc4337-wallet create devnet
./tools/chain-bench/run devnet 3
./tools/one-box down
```

创建、转账、扩展版本和运行数据说明见 [PQ 钱包文档](erc4337-wallet/README.md)。

本机密码学性能对比见 [tools/crypto-bench](tools/crypto-bench/README.md)，实验报告保存在 `docs/crypto-bench/`。
