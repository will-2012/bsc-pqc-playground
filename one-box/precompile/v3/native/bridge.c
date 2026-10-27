/* Falcon-512 TRON-body encoding. OS randomness; no upstream get_seed stub. */
#include <string.h>
#include <unistd.h>
#include <sys/random.h>
#include "backend/falcon.h"
#include "bridge.h"
static void erase(void *p,size_t n){volatile unsigned char *v=p;while(n--)*v++=0;}
static int rng_init(shake256_context *rng){
    unsigned char seed[48]; if(getentropy(seed,sizeof seed)!=0)return 0;
    shake256_init(rng);shake256_inject(rng,seed,sizeof seed);shake256_flip(rng);
    erase(seed,sizeof seed);return 1;
}
int pq_keygen(unsigned char *pk,unsigned char *sk){
    unsigned char taggedpk[897], taggedsk[1281];
    union{uint64_t align;unsigned char bytes[FALCON_TMPSIZE_KEYGEN(9)];}tmp;
    shake256_context rng;if(!rng_init(&rng))return 0;
    int rc=falcon_keygen_make(&rng,9,taggedsk,sizeof taggedsk,taggedpk,sizeof taggedpk,tmp.bytes,sizeof tmp.bytes);
    if(rc==0 && taggedpk[0]==0x09 && taggedsk[0]==0x59){memcpy(pk,taggedpk+1,896);memcpy(sk,taggedsk+1,1280);}else rc=-1;
    erase(taggedsk,sizeof taggedsk);erase(tmp.bytes,sizeof tmp.bytes);erase(&rng,sizeof rng);return rc==0;
}
int pq_sign(const unsigned char *sk,const unsigned char *msg,size_t n,unsigned char *sig,size_t *len){
    unsigned char tagged[1281];tagged[0]=0x59;memcpy(tagged+1,sk,1280);
    union{uint64_t align;unsigned char bytes[FALCON_TMPSIZE_SIGNDYN(9)];}tmp;
    int ok=0;
    for(int attempt=0;attempt<16;attempt++){
        shake256_context rng;if(!rng_init(&rng))break;
        *len=667;int rc=falcon_sign_dyn(&rng,sig,len,FALCON_SIG_COMPRESSED,tagged,sizeof tagged,msg,n,tmp.bytes,sizeof tmp.bytes);
        erase(&rng,sizeof rng);
        if(rc==FALCON_ERR_SIZE)continue;
        ok=rc==0 && *len>=617 && *len<=667 && sig[0]==0x39;break;
    }
    erase(tagged,sizeof tagged);erase(tmp.bytes,sizeof tmp.bytes);return ok;
}
int pq_verify(const unsigned char *pk,const unsigned char *msg,size_t n,const unsigned char *sig,size_t len){
    if(len<617 || len>667 || sig[0]!=0x39)return 0;
    unsigned char tagged[897];tagged[0]=0x09;memcpy(tagged+1,pk,896);
    union{uint64_t align;unsigned char bytes[FALCON_TMPSIZE_VERIFY(9)];}tmp;
    return falcon_verify(sig,len,FALCON_SIG_COMPRESSED,tagged,sizeof tagged,msg,n,tmp.bytes,sizeof tmp.bytes)==0;
}
