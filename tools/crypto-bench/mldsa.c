#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE 1
#endif
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <sys/random.h>
#include <unistd.h>
#include <openssl/crypto.h>

/* Declare the public, separately compiled ML-DSA-44 and ML-DSA-65 APIs. */
#define MLD_CONFIG_PARAMETER_SET 44
#define MLD_CONFIG_NAMESPACE_PREFIX PQCP_MLDSA_NATIVE_MLDSA44
#include "mldsa_native.h"
#undef MLD_H
#undef MLD_CONFIG_PARAMETER_SET
#undef MLD_CONFIG_NAMESPACE_PREFIX
#define MLD_CONFIG_PARAMETER_SET 65
#define MLD_CONFIG_NAMESPACE_PREFIX PQCP_MLDSA_NATIVE_MLDSA65
#include "mldsa_native.h"

/* Both cases use Pure ML-DSA, empty context and native ARM64 implementations.
 * ML-DSA-65 uses the same upstream commit as Sui's native backend, but omits
 * Sui's Rust wrappers and transaction framing. ML-DSA-44 shares TRON's
 * parameters, but this C backend is not TRON's Bouncy Castle implementation. */
typedef struct {
    int level;
    size_t pklen, siglen;
    uint8_t pk[MLDSA65_PUBLICKEYBYTES];
    uint8_t sk[MLDSA65_SECRETKEYBYTES];
} MlState;

/* getentropy accepts no more than 256 bytes on macOS and Linux. RNG calls
 * remain inside the randomized operation's timing boundary. */
int randombytes(uint8_t *out, size_t length) {
    if (out == NULL && length != 0)
        return -1;
    while (length != 0) {
        size_t chunk = length > 256 ? 256 : length;
        int rc;
        do {
            rc = getentropy(out, chunk);
        } while (rc != 0 && errno == EINTR);
        if (rc != 0)
            return -1;
        out += chunk;
        length -= chunk;
    }
    return 0;
}

void *ml_new(int level, unsigned char *pk, size_t *pklen) {
    if (level != 44 && level != 65)
        return NULL;
    size_t size = level == 44 ? MLDSA44_PUBLICKEYBYTES : MLDSA65_PUBLICKEYBYTES;
    if (pk == NULL || pklen == NULL || *pklen < size)
        return NULL;
    MlState *state = OPENSSL_zalloc(sizeof(*state));
    if (state == NULL)
        return NULL;
    state->level = level;
    state->pklen = size;
    state->siglen = level == 44 ? MLDSA44_BYTES : MLDSA65_BYTES;
    int rc = level == 44 ?
        PQCP_MLDSA_NATIVE_MLDSA44_keypair(state->pk, state->sk) :
        PQCP_MLDSA_NATIVE_MLDSA65_keypair(state->pk, state->sk);
    if (rc != 0) {
        OPENSSL_clear_free(state, sizeof(*state));
        return NULL;
    }
    memcpy(pk, state->pk, state->pklen);
    *pklen = state->pklen;
    return state;
}

void ml_free(void *opaque) {
    OPENSSL_clear_free(opaque, sizeof(MlState));
}

int ml_sign(void *opaque, const unsigned char *msg, size_t msglen,
            unsigned char *sig, size_t *siglen) {
    MlState *state = opaque;
    if (state == NULL || sig == NULL || siglen == NULL ||
        *siglen < state->siglen || (msg == NULL && msglen != 0))
        return 0;
    int rc = state->level == 44 ?
        PQCP_MLDSA_NATIVE_MLDSA44_signature(sig, msg, msglen, NULL, 0, state->sk) :
        PQCP_MLDSA_NATIVE_MLDSA65_signature(sig, msg, msglen, NULL, 0, state->sk);
    if (rc != 0)
        return 0;
    *siglen = state->siglen;
    return 1;
}

int ml_verify(void *opaque, const unsigned char *msg, size_t msglen,
              const unsigned char *sig, size_t siglen) {
    const MlState *state = opaque;
    if (state == NULL || sig == NULL || siglen != state->siglen ||
        (msg == NULL && msglen != 0))
        return 0;
    int rc = state->level == 44 ?
        PQCP_MLDSA_NATIVE_MLDSA44_verify(sig, msg, msglen, NULL, 0, state->pk) :
        PQCP_MLDSA_NATIVE_MLDSA65_verify(sig, msg, msglen, NULL, 0, state->pk);
    return rc == 0;
}

#ifdef ML_WRAPPER_TEST_MAIN
#include <stdio.h>
#include <openssl/evp.h>
#define CHECK(v) do { if(!(v)){fprintf(stderr,"ML test failed at %d\n",__LINE__);return 1;} } while(0)
static int check_level(int level){
    const char *name=level==44?"ML-DSA-44":"ML-DSA-65";size_t expected=level==44?2420:3309;
    unsigned char pk[1952],sig[3309],again[3309],msg[32]={1};size_t pl=sizeof(pk),sl=sizeof(sig),al=sizeof(again);
    void *state=ml_new(level,pk,&pl);CHECK(state&&ml_sign(state,msg,32,sig,&sl)&&sl==expected);
    CHECK(ml_sign(state,msg,32,again,&al)&&memcmp(sig,again,sl)!=0);CHECK(ml_verify(state,msg,32,sig,sl));
    CHECK(!ml_verify(state,msg,32,sig,sl-1));CHECK(!ml_verify(state,msg,32,sig,sl+1));
    EVP_PKEY *pub=EVP_PKEY_new_raw_public_key_ex(NULL,name,NULL,pk,pl);EVP_MD_CTX *md=EVP_MD_CTX_new();CHECK(pub&&md);
    CHECK(EVP_DigestVerifyInit_ex(md,NULL,NULL,NULL,NULL,pub,NULL)==1&&EVP_DigestVerify(md,sig,sl,msg,32)==1);
    pl=sizeof(pk);void *wrong=ml_new(level,pk,&pl);CHECK(wrong&&!ml_verify(wrong,msg,32,sig,sl));ml_free(wrong);
    EVP_PKEY *priv=EVP_PKEY_Q_keygen(NULL,NULL,name);CHECK(priv&&EVP_MD_CTX_reset(md)==1);
    CHECK(EVP_DigestSignInit_ex(md,NULL,NULL,NULL,NULL,priv,NULL)==1);sl=sizeof(sig);CHECK(EVP_DigestSign(md,sig,&sl,msg,32)==1);
    CHECK(EVP_PKEY_get_raw_public_key(priv,pk,&pl)==1);
    CHECK((level==44?PQCP_MLDSA_NATIVE_MLDSA44_verify(sig,msg,32,NULL,0,pk):PQCP_MLDSA_NATIVE_MLDSA65_verify(sig,msg,32,NULL,0,pk))==0);
    ml_free(state);EVP_MD_CTX_free(md);EVP_PKEY_free(pub);EVP_PKEY_free(priv);
    printf("PASS: %s bidirectional OpenSSL interoperability, randomized output, wrong key and malformed lengths\n",name);return 0;
}
int main(void){unsigned char entropy[1025];CHECK(randombytes(entropy,sizeof entropy)==0);CHECK(randombytes(NULL,0)==0);CHECK(randombytes(NULL,1)!=0);CHECK(check_level(44)==0&&check_level(65)==0);return 0;}
#endif
