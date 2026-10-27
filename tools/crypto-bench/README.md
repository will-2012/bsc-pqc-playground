# Go 签名算法对比

用于比较 BSC 可能采用的签名算法。所有正式计时由 `main.go` 执行，统一 Go `Sign` / `Verify` 接口，每次操作调用一次 C 后端，计入 Go 和 CGO 开销。`run.py` 只负责依赖构建、顺序调度、校验和报告，不执行密码学计算。

当前组合：ECDSA-secp256k1、原版 Falcon-512、ML-DSA-44、ML-DSA-65、SLH-DSA-SHA2-128s / 128f。备注说明 TRON / Sui 的采用场景；结果不是它们的客户端或整链性能。

## 运行

依赖原生 Go、Python 3.12+、Clang、CMake、Make、Perl。下载的源码、库、二进制、旧结果和临时文件都在 Git 忽略的 `.runtime/`；不会安装系统软件或启动开发链。

```sh
python3 tools/crypto-bench/run.py --smoke
python3 tools/crypto-bench/run.py --profile macos-arm64
python3 tools/crypto-bench/run.py --verify
```

默认每项 5 轮，每轮 7 批，签名每批 32 次、验签每批 256 次。每算法正式签名 1,120 次、验签 8,960 次。语料池包含 4,096 条 32 bytes 消息，各算法使用相同索引。本轮签名覆盖 1,120 条消息；验签每轮循环最后 32 对样本，五轮共覆盖 160 条消息。调用次数相同不表示用尽整个语料池。

结果只发布一份当前报告和各平台最终数据：

```text
docs/crypto-bench/
  comparison.html
  macos-arm64/
    results.json   # 原始样本、统计、环境、源码快照和构建信息
    checks.txt     # 正确性与上游测试日志
```

旧试跑与历史数据在 `.runtime/`，不与最终报告混放；测试不导出私钥。

## 平台

- `macos-arm64`：当前实测平台，Apple M1 Pro；Clang `-O3 -mcpu=apple-m1 -DNDEBUG -fno-fast-math -ffp-contract=off`。
- `linux-x86_64`：配置已独立，计划用于 Intel 服务器；x86 Falcon 后端尚未接入和验证，脚本会拒绝运行，不把 ARM 构建或推算值当 Linux 成绩。

缓存、构建身份与结果均以平台隔离。实际 CPU 型号、操作系统、Go / 编译器版本和指令能力写入结果。macOS 不保证固定性能核或锁频；正式测试前关闭 devnet、Bundler 和其他重负载，接通电源。

## 口径

- 同一 Go 程序、`GOMAXPROCS(1)`、`LockOSThread`；预分配缓冲区，计时期间禁用 Go GC。每次 Sign / Verify 一次 CGO，不在 C 中批量摊薄调用成本。
- 主值是线程 CPU；同步记录墙钟和进程 CPU。批量均值先按轮取中位数，再对各轮取中位数。签名、验签分别对本次 ECDSA baseline 归一化。
- ECDSA 输入为 32 bytes 摘要，测已知公钥验签而非恢复公钥；PQ 使用同一 32 bytes 原始消息，Pure / 空上下文。内部哈希、随机数、拒绝采样和编码计入。
- 使用正常安全随机源：ECDSA 是标准确定性 nonce；ML 和 Falcon 直接读取操作系统随机数；SLH 使用 OpenSSL 安全 DRBG。需求量和内部路径不同，不宣称完全相同的随机计算成本。
- 密钥生成与可复用对象创建不计入主值，单独记录；SLH 每次调用的 EVP reset / Init 仍计时。每轮新密钥，顺序轮转。上下文、已展开的 ML 密钥可复用，Falcon 为每次解码的动态签名；本次是热密钥、小工作集场景。
- 存储列是序列化编码长度，不是对象内存或链上存储 gas。Sui 钱包 32 bytes seed 与 ML-65 的 4,032 bytes 展开私钥是不同表示；Falcon 采用 TRON 无头公钥 / 私钥主体 896 / 1,280 bytes。617–667 bytes 是本适配器接受的签名长度，不是通用 Falcon 长度上限或本轮观测范围，也不是 TVM 的固定 666 bytes 槽。
- 每轮检查 32 个正例与四类负例；每个计时调用都检查返回，签名每批末复验。默认 32 次签名时，全部计时输出都被复验；调大批次数时只保留最后 32 个输出。

## 依赖与审核

`sources.json` 固定仓库提交与归档 SHA256。ECDSA 为 libsecp256k1，ML 两参数为同一 mldsa-native，Falcon 为原版 Falcon 的 ARM NEON 实现，SLH 为 OpenSSL C / ARM SHA-256。无 Java、Rust 或 C++ 包装进入正式测试。

Falcon 上游的 `get_seed()` 是论文 benchmark 固定值 stub，适配器明确绕过它，每次签名使用系统新熵；关闭显式复数融合乘加特化，保留 NEON 与正常浮点语义。公开 Falcon KAT 和上游 NIST KAT 已核对，ML 两参数与 OpenSSL 交叉验签。完整边界、源码版本和测量代码快照随结果保存。

```sh
python3 -B -m unittest discover -s tools/crypto-bench -p 'test_*.py' -v
```

统一 Go 接口保证相同外层调用边界；不同算法的数学结构、内部缓存和架构优化仍有差别。公开评测用于核对量级及取舍，不用于强制排名。完整交易与实际预编译见 [链路报告](../../docs/chain-bench/report.html)。该报告的 SLH 后端是 Go，主时钟为进程 CPU，ECDSA 基准为公钥恢复；不能直接套用本报告的线程 CPU 倍数定价。交叉复核运行 `python3 tools/chain-bench/audit.py`。
