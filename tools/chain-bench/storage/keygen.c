/* Streaming deterministic key generation. Seeds arrive on stdin, never argv.
 * Uses the same pinned OpenSSL source as crypto-bench. Output is public only. */
#include <stdio.h>
#include <string.h>
#include <openssl/evp.h>
#include <openssl/params.h>
#include <openssl/crypto.h>
#include <openssl/err.h>
int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "ML-DSA-44") && strcmp(argv[1], "SLH-DSA-SHA2-128s"))) return 2;
    char line[132]; unsigned char seed[64], pk[2048];
    while (fgets(line, sizeof(line), stdin)) {
        size_t n = strcspn(line, "\r\n"), len = sizeof(pk);
        if (n != (strcmp(argv[1], "ML-DSA-44") ? 96 : 64)) return 3;
        for (size_t i = 0; i < n/2; i++) { unsigned v; if (sscanf(line+2*i, "%2x", &v)!=1) return 3; seed[i]=v; }
        EVP_PKEY_CTX *ctx = EVP_PKEY_CTX_new_from_name(NULL, argv[1], NULL);
        EVP_PKEY *key = NULL;
        OSSL_PARAM params[] = {OSSL_PARAM_construct_octet_string("seed", seed, n/2), OSSL_PARAM_construct_end()};
        if (!ctx || EVP_PKEY_keygen_init(ctx)!=1 || EVP_PKEY_CTX_set_params(ctx,params)!=1 || EVP_PKEY_generate(ctx,&key)!=1 || EVP_PKEY_get_raw_public_key(key,pk,&len)!=1) { ERR_print_errors_fp(stderr); return 4; }
        for (size_t i=0;i<len;i++) printf("%02x",pk[i]);
        puts(""); fflush(stdout);
        EVP_PKEY_free(key); EVP_PKEY_CTX_free(ctx); OPENSSL_cleanse(seed,sizeof(seed)); OPENSSL_cleanse(line,sizeof(line));
    }
    return ferror(stdin) ? 5 : 0;
}
