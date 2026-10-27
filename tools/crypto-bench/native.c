/* C backends behind the common Go API. No key material leaves an opaque key.
 * Timed calls perform one complete signature or verification, never a batch. */
#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>
#include <secp256k1.h>
#ifdef __APPLE__
#include <pthread.h>
#include <pthread/qos.h>
#endif
#include "native.h"
void *ml_new(int,unsigned char *,size_t *);
void ml_free(void *);
int ml_sign(void *,const unsigned char *,size_t,unsigned char *,size_t *);
int ml_verify(void *,const unsigned char *,size_t,const unsigned char *,size_t);
void *falcon_new(unsigned char *,size_t *);
void falcon_free(void *);
int falcon_sign(void *,const unsigned char *,size_t,unsigned char *,size_t *);
int falcon_verify(void *,const unsigned char *,size_t,const unsigned char *,size_t);
struct BenchKey {
    int id;void *key;secp256k1_context *ec;secp256k1_pubkey epk;
    unsigned char sk[32],pk[2000];size_t pklen;
    EVP_PKEY *public_key,*evp_key;EVP_MD_CTX *verify_ctx,*sign_ctx;
};
int bench_setup_thread(void){
#ifdef __APPLE__
    return pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED,0)==0;
#else
    return 1;
#endif
}
static uint64_t clock_ns(clockid_t id){struct timespec t;if(clock_gettime(id,&t)!=0)abort();return (uint64_t)t.tv_sec*1000000000ULL+(uint64_t)t.tv_nsec;}
uint64_t bench_thread_cpu_ns(void){return clock_ns(CLOCK_THREAD_CPUTIME_ID);}
uint64_t bench_process_cpu_ns(void){return clock_ns(CLOCK_PROCESS_CPUTIME_ID);}
const char *bench_cpu_features(void){return OpenSSL_version(OPENSSL_CPU_INFO);}
static const char *evp_names[]={"","","ML-DSA-44","ML-DSA-65","SLH-DSA-SHA2-128s","SLH-DSA-SHA2-128f"};
void bench_free(BenchKey *s){
    if(!s)return;
    if(s->key){if(s->id==1)falcon_free(s->key);else ml_free(s->key);}
    if(s->ec)secp256k1_context_destroy(s->ec);
    EVP_PKEY_free(s->public_key);EVP_PKEY_free(s->evp_key);EVP_MD_CTX_free(s->verify_ctx);EVP_MD_CTX_free(s->sign_ctx);
    OPENSSL_clear_free(s,sizeof(*s));
}
BenchKey *bench_new(int id){
    if(id<0||id>=6)return NULL;
    BenchKey *s=OPENSSL_zalloc(sizeof(*s));if(!s)return NULL;s->id=id;s->pklen=sizeof(s->pk);
    if(id==0){
        s->ec=secp256k1_context_create(SECP256K1_CONTEXT_NONE);if(!s->ec)goto fail;
        do{if(RAND_bytes(s->sk,32)!=1)goto fail;}while(!secp256k1_ec_seckey_verify(s->ec,s->sk));
        unsigned char seed[32];if(RAND_bytes(seed,32)!=1)goto fail;
        int randomized=secp256k1_context_randomize(s->ec,seed);OPENSSL_cleanse(seed,32);if(!randomized)goto fail;
        if(!secp256k1_ec_pubkey_create(s->ec,&s->epk,s->sk))goto fail;s->pklen=33;
        if(!secp256k1_ec_pubkey_serialize(s->ec,s->pk,&s->pklen,&s->epk,SECP256K1_EC_COMPRESSED))goto fail;
    }else if(id==1){s->key=falcon_new(s->pk,&s->pklen);if(!s->key)goto fail;}
    else{
        if(id<=3){s->key=ml_new(id==2?44:65,s->pk,&s->pklen);if(!s->key)goto fail;}
        else{
            s->evp_key=EVP_PKEY_Q_keygen(NULL,NULL,evp_names[id]);if(!s->evp_key)goto fail;
            if(EVP_PKEY_get_raw_public_key(s->evp_key,s->pk,&s->pklen)!=1)goto fail;
            s->sign_ctx=EVP_MD_CTX_new();if(!s->sign_ctx)goto fail;
        }
        s->public_key=EVP_PKEY_new_raw_public_key_ex(NULL,evp_names[id],NULL,s->pk,s->pklen);
        s->verify_ctx=EVP_MD_CTX_new();if(!s->public_key||!s->verify_ctx)goto fail;
    }return s;
fail:bench_free(s);return NULL;
}
size_t bench_public_key(BenchKey *s,unsigned char *out,size_t cap){if(!s||cap<s->pklen)return 0;memcpy(out,s->pk,s->pklen);return s->pklen;}
int bench_sign(BenchKey *s,const unsigned char *msg,size_t n,unsigned char *out,size_t *len){
    if(!s||!msg||!out||!len)return 0;
    if(s->id==0){if(n!=32||*len<64)return 0;secp256k1_ecdsa_signature sig;if(!secp256k1_ecdsa_sign(s->ec,&sig,msg,s->sk,NULL,NULL))return 0;*len=64;return secp256k1_ecdsa_signature_serialize_compact(s->ec,out,&sig);}
    if(s->id<=3)return s->id==1?falcon_sign(s->key,msg,n,out,len):ml_sign(s->key,msg,n,out,len);
    return EVP_MD_CTX_reset(s->sign_ctx)==1&&EVP_DigestSignInit_ex(s->sign_ctx,NULL,NULL,NULL,NULL,s->evp_key,NULL)==1&&EVP_DigestSign(s->sign_ctx,out,len,msg,n)==1;
}
int bench_verify(BenchKey *s,const unsigned char *msg,size_t n,const unsigned char *sig,size_t len){
    if(!s||!msg||!sig)return 0;
    if(s->id==0){secp256k1_ecdsa_signature parsed;return n==32&&len==64&&secp256k1_ecdsa_signature_parse_compact(s->ec,&parsed,sig)&&secp256k1_ecdsa_verify(s->ec,&parsed,msg,&s->epk);}
    if(s->id<=3)return s->id==1?falcon_verify(s->key,msg,n,sig,len):ml_verify(s->key,msg,n,sig,len);
    return EVP_MD_CTX_reset(s->verify_ctx)==1&&EVP_DigestVerifyInit_ex(s->verify_ctx,NULL,NULL,NULL,NULL,s->public_key,NULL)==1&&EVP_DigestVerify(s->verify_ctx,sig,len,msg,n)==1;
}
int bench_cross_verify(BenchKey *s,const unsigned char *msg,size_t n,const unsigned char *sig,size_t len){
    if(s->id!=2&&s->id!=3)return bench_verify(s,msg,n,sig,len);
    return EVP_MD_CTX_reset(s->verify_ctx)==1&&EVP_DigestVerifyInit_ex(s->verify_ctx,NULL,NULL,NULL,NULL,s->public_key,NULL)==1&&EVP_DigestVerify(s->verify_ctx,sig,len,msg,n)==1;
}
int bench_sign_into(BenchKey *key,const unsigned char *msg,size_t n,unsigned char *out,size_t cap){size_t len=cap;return bench_sign(key,msg,n,out,&len)?(int)len:0;}
/* Public standard vectors only; this is outside every timing window. */
#include <openssl/params.h>
int bench_standard_verify(const char *algorithm,const unsigned char *pk,size_t pklen,const unsigned char *msg,size_t mlen,const unsigned char *sig,size_t slen,unsigned char *context,size_t clen){
    unsigned char empty=0;if(mlen==0)msg=&empty;if(clen==0)context=&empty;
    EVP_PKEY *key=EVP_PKEY_new_raw_public_key_ex(NULL,algorithm,NULL,pk,pklen);EVP_MD_CTX *md=EVP_MD_CTX_new();
    if(!key||!md){EVP_PKEY_free(key);EVP_MD_CTX_free(md);return 0;}
    OSSL_PARAM params[]={OSSL_PARAM_octet_string("context-string",context,clen),OSSL_PARAM_END};
    int valid=EVP_DigestVerifyInit_ex(md,NULL,NULL,NULL,NULL,key,params)==1&&EVP_DigestVerify(md,sig,slen,msg,mlen)==1;
    EVP_PKEY_free(key);EVP_MD_CTX_free(md);return valid;
}
