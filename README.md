# BSC PQC Playground

本仓库用于在 BSC 上开展抗量子密码学（PQC）相关实验。

- `external/`：固定版本的 BSC、创世合约和 Alto 源码，通过 Git submodule 管理。
- `contract/`：ERC-4337 相关合约源码、依赖和编译配置。
- `one-box/`：本地开发链的配置模板、内部脚本、测试、文档和运行数据。
- `tools/one-box`：开发链的统一入口。

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

合约说明见 [contract 文档](contract/README.md)。当前验证的是 ECDSA 智能账户链路，为后续 PQ 实验提供基线。

本机密码学性能对比见 [tools/crypto-bench](tools/crypto-bench/README.md)，实验报告保存在 `docs/crypto-bench/`。
