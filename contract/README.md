# 合约

这里存放合约源码、依赖锁文件和编译配置。ECDSA 基线通过 `tools/one-box` 调用内部脚本 `one-box/scripts/erc4337.mjs`；PQ 各版通过 `tools/erc4337-wallet` 调用。

| 合约 | 来源与用途 |
| --- | --- |
| EntryPoint v0.7 | `@account-abstraction/contracts@0.7.0`；处理账户创建、验证、nonce、费用及执行 |
| SimpleAccountFactory / SimpleAccount | 同一上游包的标准示例；本阶段使用 ECDSA owner，作为 ERC-4337 基线 |
| Counter | 本仓库的测试合约，记录调用次数和直接调用者 |
| PQAccountFactory | 按公钥和 salt 计算 CREATE2 地址，创建不可升级的 PQAccount；重复调用复用已有账户 |
| PQAccount | 基于上游 BaseAccount v0.7；固定 SLH 公钥和验证器，仅 EntryPoint 可验证授权、执行调用 |
| SLHDSA128s | Solidity 完整验证 SLH-DSA-SHA2-128s；仅使用现有 SHA-256 预编译，无新增 PQ 预编译 |
| SLHDSA128sPrecompile | v2 验证器适配器；以同一 verify ABI 调用本地客户端的 `0x0101` 预编译 |

目录按公共合约和版本划分：

```text
src/common/   PQAccount、PQAccountFactory、Counter、上游合约导入
src/v1/       SLHDSA128s，Solidity 完整验签
src/v2/       SLHDSA128sPrecompile，原生预编译适配器
src/v3/       FalconAccount、FalconAccountFactory、FalconPrecompile
src/v4/       MLDSAAccount、MLDSAAccountFactory、MLDSAPrecompile
src/v5/       MLDSA65Account、MLDSA65AccountFactory、MLDSA65Precompile
src/v6/       SLHDSA128fPrecompile
```

SLH 各版复用公共账户与工厂源码，分别绑定各自验证器。`src/common/Imports.sol` 引入上游实现，EntryPoint 保持不变。SimpleAccount 是 ECDSA 基线；PQAccount 使用独立的 PQ 授权逻辑。固定 Solidity 0.8.23、Paris 指令集和 OpenZeppelin 5.0.2，依赖由 `package-lock.json` 锁定。

```bash
# 从仓库根目录执行；同时构建 Alto、安装依赖、编译合约
./tools/one-box bundler build

# 启动开发链，部署合约，启动 Bundler
./tools/one-box bundler up
./tools/one-box bundler test
```

合约使用生成的实验账户正常部署，地址随环境变化，不要求以太坊公共网络的标准部署地址。地址和交易记录保存在 `one-box/.runtime/<环境名>/bundler/deployment.json`；这里不存放私钥或运行时部署配置。

PQ 合约由 [`erc4337-wallet`](../erc4337-wallet/README.md) 部署，记录位于 `one-box/.runtime/<环境名>/wallet/<profile>/deployment.json`。`slh-dsa-v1` 保留原 Solidity 验证器；`slh-dsa-v2` 部署新的 adapter 和工厂，但复用相同的 PQAccount / PQAccountFactory 源码。各账户绑定的验证器不同，不会替换已有 v1 合约。

v1 / v2 验证器采用 FIPS 205 的 `n=16, h=63, d=7, h'=9, a=12, k=14, w=16`。公钥 32 bytes、签名 7,856 bytes。只接受 32 bytes 的 UserOperation hash，采用 Pure 模式、空 context，即签名消息封装为 `0x0000 || userOpHash`；不是 HashSLH-DSA 或旧 SPHINCS+ 编码。计算 FORS 根后，依次验证七层 WOTS+ / XMSS，最终与公钥根比较。签名长度不匹配直接返回 false。

公钥通过 `immutable` 嵌入账户代码，验证器与 EntryPoint 地址也不可更改。账户没有 ECDSA owner、代理管理员、恢复后门或密钥轮换。签名绑定 EntryPoint 计算的 hash，覆盖 chain ID、EntryPoint、sender、nonce、调用参数和费用。工厂可由任何人调用，但无法改变给定公钥与 salt 对应账户的权限。

这是实验实现，未经过独立安全审计或 FIPS 实现认证。算法依据为 [FIPS 205](https://doi.org/10.6028/NIST.FIPS.205)；测试包含 noble 与 OpenSSL 独立签名互验，不能据此宣称形式证明或生产安全。

v2 的原生输入固定为 `publicKey[32] || userOpHash[32] || signature[7856]`，返回 32 bytes 的 0/1。adapter 在调用失败、返回长度不对或无预编译时返回 false，不会静默切换到 v1。`0x0101` 是本地实验地址，不是 BSC 主网预编译。价格为固定 60,000 gas，外层 calldata 和合约调用费用另计，详见 [链路报告](../docs/chain-bench/README.md)。

## v3：Falcon 账户

Falcon 公钥为 896-byte 主体，不能直接使用 common/PQAccount 的 bytes32 公钥。v3 使用独立 FalconAccountFactory / FalconAccount，保持固定 EntryPoint、固定验证器、仅 EntryPoint 可执行的权限模型；无 ECDSA owner、代理、恢复或密钥修改接口。工厂将完整公钥与 salt 绑定到 CREATE2 地址。

公钥在构造时写入 bytes 存储，共 29 个槽。FalconPrecompile 以 `publicKey[896] || userOpHash[32] || signature[617..667]` 调用 `0x0102`，长度、调用或返回值不合法均返回 false，不回退到其他算法。链上保存公钥会显著增加创建及读取成本，本版未做代码存储或承诺优化。

原有 SLH 合约与字节码保留。v3 的首次验证预算超过常见公共内存池预算，不能仅因签名更短就宣称所有公共 Bundler 可用。

## v4：ML-DSA-44 账户

`src/v4/` 包含 MLDSAAccount、MLDSAAccountFactory、MLDSAPrecompile。账户权限边界沿用 v3；公钥为 1,312 bytes，以 bytes 存储，共 42 个槽（一个长度槽、41 个数据槽）。工厂通过公钥与 salt 绑定 CREATE2 地址，不接受错误公钥长度。验证器只接受固定 2,420-byte 签名，并以 `publicKey[1312] || userOpHash[32] || signature[2420]` 调用 `0x0103`，失败时返回 false。

这是 FIPS 204 Pure ML-DSA-44、空 context 的本地预编译路径；合约不实现格运算，不回退到 ECDSA 或其他算法。算法已标准化不表示本仓库的客户端与合约经过认证或审计。

## v5：ML-DSA-65 账户

`src/v5/` 包含 MLDSA65Account、MLDSA65AccountFactory、MLDSA65Precompile。权限模型与 v4 相同，但固定公钥长度为 1,952 bytes，占 62 个存储槽（一个长度槽与 61 个数据槽）；签名为 3,309 bytes。adapter 使用 `0x0104`，原生输入为 `publicKey[1952] || userOpHash[32] || signature[3309]`。本版没有替换 v4 合约、改变旧账户权限或引入算法回退。

## v6：SLH-DSA-SHA2-128f

`src/v6/SLHDSA128fPrecompile.sol` 以 `0x0105` 验证固定 17,088-byte 签名；账户与工厂复用 common 中的 bytes32 公钥版本，公钥通过 immutable 嵌入代码。新部署绑定新的验证器，不改变 v1/v2 账户。历史接口名 `ISLHDSA128s` 仅定义通用的 verify ABI，复用它不表示 v6 使用 128s 算法。无算法回退、管理员或换钥接口。
