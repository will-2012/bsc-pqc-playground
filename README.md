# BSC PQC Playground

本仓库用于在 BSC 上开展抗量子密码学（PQC）相关实验。

- `external/`：固定版本的 BSC 和创世合约源码，通过 Git submodule 管理。
- `one-box/`：本地开发链的配置模板、内部脚本、测试、文档和运行数据。
- `tools/one-box`：开发链的统一入口。

```bash
git submodule update --init --recursive
./tools/one-box doctor
./tools/one-box build
./tools/one-box up
```

使用方式、初始资金和配置说明见 [one-box 文档](one-box/README.md)。
