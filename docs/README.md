# 实验报告

- [Go 签名算法对比](crypto-bench/comparison.html)：当前 macOS ARM 的最终结果、算法使用方和测试口径。
- [复现方法](../tools/crypto-bench/README.md)：统一 Go 接口、依赖与平台配置。
- [ERC-4337 链路 Bench](chain-bench/README.md)：v1–v6，SLH-128s / 128f、Falcon、ML-DSA-44 / 65 与原生 EOA 的 gas 对比，以及预编译性能和实验定价。
- [Bench 性能排查](chain-bench/load/performance.html)：ECDSA / ML / SLH 摸底汇总、可复用优化及瓶颈判断。

- [技术调研](research/README.md)：以太坊抗量子路线与相关研究。

链路对比也可查看 [HTML 报告](chain-bench/report.html)。

- [x86_64 预编译定价复核](chain-bench/pricing/linux-x86_64/report.html)：空闲 AMD 节点，实际部署与 AVX2 后端、ECRECOVER 基线和 gas 校准。
