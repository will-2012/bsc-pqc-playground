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

合约说明见 [contract 文档](contract/README.md)。合约和钱包按 `common/`、`v1/`、`v2/`、`v3/`、`v4/`、`v5/` 组织：公共实现复用，版本差异独立。保留 ECDSA 基线，以及两个互不替换的 SLH-DSA profile：v1 为 Solidity 验签（默认），v2 为新增预编译。

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

Falcon-512 v3 的预编译、共同 PQ 硬分叉、钱包与并发测试见 [HTML 对比报告](docs/chain-bench/report.html)；[运行步骤](tools/chain-bench/README.md#v3falcon-512-与同链对照)。

ML-DSA-44 v4 沿用共同 PQ 硬分叉，新增预编译、独立账户和钱包 profile；[运行步骤](tools/chain-bench/README.md#v4ml-dsa-44)与[链路报告](docs/chain-bench/report.html)。

ML-DSA-65 v5 使用类别 3 参数，独立账户与预编译，仍复用同一 PQ 分叉；[运行步骤](tools/chain-bench/README.md#v5ml-dsa-65-与-ml-dsa-44-同链对照)。
