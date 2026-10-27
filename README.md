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

合约说明见 [contract 文档](contract/README.md)。合约和钱包按 `common/`、`v1/`、`v2/`、`v3/`、`v4/`、`v5/`、`v6/` 组织：公共实现复用，版本差异独立。保留 ECDSA 基线，以及互不替换的 SLH-DSA profile：v1 为 Solidity 验签（默认），v2 为 128s 预编译，v6 为 128f 预编译。

运行 v1 PQ 全链路（Solidity 验签）：

```bash
./tools/one-box bundler build
./tools/one-box bundler up --pq
./tools/erc4337-wallet create devnet
./tools/chain-bench/run devnet 3
./tools/one-box down
```

创建、转账、扩展版本和运行数据说明见 [PQ 钱包文档](erc4337-wallet/README.md)。

v2 的独立客户端构建和环境准备见 [one-box](one-box/README.md#v2-预编译实验环境)。[链路报告](docs/chain-bench/README.md) 对比 v1 / v2 / 原生 EOA 的完整交易 gas，并记录预编译性能与定价。

本机密码学性能对比见 [tools/crypto-bench](tools/crypto-bench/README.md)，实验报告保存在 `docs/crypto-bench/`。

v2–v6 分别覆盖 SLH-128s、Falcon-512、ML-DSA-44、ML-DSA-65 和 SLH-128f。版本选择与同链复现见 [chain-bench](tools/chain-bench/README.md)，结果见 [HTML 报告](docs/chain-bench/report.html)。
