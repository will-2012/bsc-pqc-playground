# x86_64 预编译定价复核

比较实际 BSC 客户端的 ECRECOVER、ML-DSA-44 和 SLH-DSA-SHA2-128s `Run`，以 ECRECOVER 的 3,000 gas 为计算成本基线。签名和已知公钥 ECDSA 验签只作参照，不参与定价。

## 构建

先按 `one-box/remote/README.md` 构建 Linux 集群客户端。以下操作只生成独立测试程序，不部署或启动节点：

```sh
python3 tools/chain-bench/pricing/build.py \
  --output one-box/.runtime/build/pricing/deployed
python3 tools/chain-bench/pricing/build.py --optimized-ml \
  --output one-box/.runtime/build/pricing/avx2
```

构建复制已有客户端源码，不修改原静态库。默认保留部署使用的 ML 可移植后端；`--optimized-ml` 以同一固定来源构建 AVX2 后端，并执行上游功能测试和 KAT。两者的 Go `Run` 代码一致。ECDSA 沿用 BSC 的 libsecp256k1；SLH 沿用 Go 后端及其 SHA-NI / AVX2 运行时分派。`GOAMD64=v1` 保证 Go 基础指令兼容性，不会关闭这些分派。

ML 优化编译参数为 `-O3 -mavx2 -mbmi2 -mpopcnt -mtune=generic -fno-fast-math`，没有按中控机器使用 `-march=native`。编译、依赖及源码摘要写入 `build.json`；原始构建日志留在输出目录。

## 测量

选一台空闲的 Linux x86_64 机器，将输出中的 `vm.test`、`testdata/`、`build.json` 及本目录 `run.py` 一起放到私有目录。目标机只需 Python 3.9+；构建机还需 Python 3.11+、Go、GCC 和 Make。使用目标机实际允许的 CPU 编号：

```sh
python3 run.py --binary vm.test --fixtures . \
  --output results-core2 --backend deployed-portable --cpu 2 --rounds 7
# 在另一颗核复测；另以 --gc 50 检查部署 GC 参数的影响。
python3 run.py --binary vm.test --fixtures . \
  --output results-core5 --backend deployed-portable --cpu 5 --rounds 7
```

优化二进制使用 `--backend mldsa-avx2` 标记，并单独保存结果。输出目录必须是新目录，避免覆盖历史样本。

- 单进程、单执行 P、锁定 OS 线程，并绑定一颗 CPU；各组顺序运行。默认 GC=100，保持 GC 开启。
- 各算法处理相同来源的 32 bytes 消息。SLH 使用公开多密钥向量；ML 每次在计时外生成两把密钥、对同一消息集合签名，并检查独立标准向量。结果对应热密钥、小工作集。
- 每轮按固定种子重新排列 19 个测试项，每项计时 1 s，至少五轮。记录进程 CPU、线程 CPU、墙钟与 Go 分配；C 内存不计入 Go B/op。
- 正例及错消息、尾字节篡改、零签名、确定性伪随机签名、截短、加长路径均检查结果；现有用例验证恰好 gas 可执行、少 1 gas 失败。签名在计时前后验签，密钥生成与结果验签不计时。
- 沿用原实验公式：已测验签路径最大轮均值 ÷ ECRECOVER 中位数 × 3,000 × 1.25，先向上取整到 1,000，再到 10,000 gas。这是局部实验规则，不是 NIST 或以太坊规定的 PQ 定价标准，也不是最坏单次输入上界。

当前部署后端决定当前价格是否覆盖实测成本；优化后端用于评估实现空间，不自动改变链上价格。机器、后端及统计口径不同的结果分列，不覆盖原 macOS 报告。

测试：`python3 -B -m unittest discover -s tools/chain-bench/pricing -p 'test_*.py'`。

报告验算：`python3 tools/chain-bench/pricing/verify.py docs/chain-bench/pricing/linux-x86_64/results.json`。HTML 可由 `report.py --data ... --output ...` 重新生成；原始样本和源码快照保留在数据文件中。
