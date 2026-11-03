/* Bench-only persistent signer. stdin: secret key[64] || message[32];
 * stdout: signature[7856]. Pure SLH-DSA-SHA2-128s, empty context.
 * No key material in argv, logs or temporary files. EOF ends the process. */
#include <openssl/evp.h>
#include <openssl/crypto.h>
#include <stdio.h>
#include <sys/resource.h>
int main(void) {
    struct rlimit limit = {0, 0};
    if (setrlimit(RLIMIT_CORE, &limit) != 0) return 1;
    unsigned char input[96], signature[7856];
    int result = 0;
    for (;;) {
        size_t n = fread(input, 1, sizeof(input), stdin);
        if (n == 0 && feof(stdin)) break;
        if (n != sizeof(input)) { result = 1; break; }
        EVP_PKEY *key = EVP_PKEY_new_raw_private_key_ex(NULL, "SLH-DSA-SHA2-128s", NULL, input, 64);
        EVP_PKEY *pub = EVP_PKEY_new_raw_public_key_ex(NULL, "SLH-DSA-SHA2-128s", NULL, input + 32, 32);
        EVP_MD_CTX *sign = EVP_MD_CTX_new(), *verify = EVP_MD_CTX_new();
        size_t size = sizeof(signature);
        int ok = key && pub && sign && verify &&
            EVP_DigestSignInit_ex(sign, NULL, NULL, NULL, NULL, key, NULL) == 1 &&
            EVP_DigestSign(sign, signature, &size, input + 64, 32) == 1 && size == sizeof(signature) &&
            EVP_DigestVerifyInit_ex(verify, NULL, NULL, NULL, NULL, pub, NULL) == 1 &&
            EVP_DigestVerify(verify, signature, size, input + 64, 32) == 1;
        EVP_MD_CTX_free(sign); EVP_MD_CTX_free(verify);
        EVP_PKEY_free(key); EVP_PKEY_free(pub);
        OPENSSL_cleanse(input, sizeof(input));
        if (!ok || fwrite(signature, 1, size, stdout) != size || fflush(stdout) != 0) { result = 1; break; }
    }
    OPENSSL_cleanse(input, sizeof(input));
    OPENSSL_cleanse(signature, sizeof(signature));
    if (result) fputs("SLH signer failed\n", stderr);
    return result;
}
