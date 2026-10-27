# ML-DSA-65 公共验签向量

`mldsa65.tsv` 提取 OpenSSL 固定版本 `evppkey_ml_dsa_sigver.txt` 中的 `ML_DSA_65_42`：NIST ACVP FIPS 204 Pure ML-DSA-65、空 context 的有效签名。仅包含算法名、公钥、消息、签名、context 占位符和期望值，无私钥。固定提交见上级 `dependency.json`。

原始数据：Copyright 2025 The OpenSSL Project Authors，Apache License 2.0；https://www.openssl.org/source/license.html 。上游来源为 NIST ACVP-Server 的 ML-DSA-sigVer-FIPS204 测试数据。
