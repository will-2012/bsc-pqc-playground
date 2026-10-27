/* Original Falcon-512 (TRON-compatible), NOT the 2026 FN-DSA candidate.
 * Backend: GMUCERG/FALCON_NEON 1d2670077d14de81a9b66b4c6494a67256342cd2,
 * falcon-armv8/neon. Requires -Dfalcon_verify=falcon_neon_verify when compiling
 * upstream sources, -DFALCON_LOGN=9 -DAPPLE_M1=0 and strict floating-point.
 * FMA=0 and FALCON_FPEMU=0 in pinned config.h; APPLE_M1=0 also disables
 * explicit complex fused multiply-add specializations. NEON stays enabled.
 * No secret-dependent precomputation
 * is moved outside signing: dynamic signing decodes its private key each call.
 * Persistent state holds encoded keys and a reusable temporary buffer only.
 * IMPORTANT: the upstream get_seed() is a benchmark stub. Never call it.
 * Our keygen and EVERY signing attempt use fresh getentropy(), SHAKE + flip.
 * Internal tagged pk/sk are 897/1281 bytes; TRON bodies are 896/1280 bytes.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/random.h>
#define falcon_verify falcon_neon_verify
#include "falcon.h"
#undef falcon_verify

typedef struct {
    unsigned char private_key[FALCON_PRIVKEY_SIZE(9)];
    unsigned char public_key[FALCON_PUBKEY_SIZE(9)];
    union { uint64_t align; unsigned char bytes[FALCON_TMPSIZE_SIGNDYN(9)]; } tmp;
} FalconState;
static void erase(void *p,size_t n){volatile unsigned char *v=p;while(n--)*v++=0;}
static int fresh_rng(shake256_context *rng){
    unsigned char seed[48];
    if(getentropy(seed,sizeof seed)!=0)return 0;
    shake256_init(rng);shake256_inject(rng,seed,sizeof seed);shake256_flip(rng);
    erase(seed,sizeof seed);return 1;
}
void falcon_free(void *opaque){if(opaque){erase(opaque,sizeof(FalconState));free(opaque);}}
void *falcon_new(unsigned char *pk,size_t *pklen){
    if(!pk||!pklen||*pklen<896)return NULL;
    FalconState *s=calloc(1,sizeof *s);if(!s)return NULL;
    shake256_context rng;
    if(!fresh_rng(&rng)){falcon_free(s);return NULL;}
    int rc=falcon_keygen_make(&rng,9,s->private_key,sizeof s->private_key,s->public_key,sizeof s->public_key,s->tmp.bytes,sizeof s->tmp.bytes);
    erase(&rng,sizeof rng);
    if(rc!=0||s->private_key[0]!=0x59||s->public_key[0]!=0x09){falcon_free(s);return NULL;}
    memcpy(pk,s->public_key+1,896);*pklen=896;return s;
}
int falcon_sign(void *opaque,const unsigned char *msg,size_t msglen,unsigned char *sig,size_t *siglen){
    if(!opaque||!msg||!sig||!siglen||*siglen<667)return 0;
    FalconState *s=opaque;
    for(int attempt=0;attempt<16;attempt++){
        shake256_context rng;if(!fresh_rng(&rng))return 0;
        size_t len=667;
        int rc=falcon_sign_dyn(&rng,sig,&len,FALCON_SIG_COMPRESSED,s->private_key,sizeof s->private_key,msg,msglen,s->tmp.bytes,sizeof s->tmp.bytes);
        erase(&rng,sizeof rng);
        if(rc==FALCON_ERR_SIZE)continue;
        if(rc!=0)return 0;
        if(len>=617&&len<=667&&sig[0]==0x39){*siglen=len;return 1;}
    }
    return 0;
}
int falcon_verify(void *opaque,const unsigned char *msg,size_t msglen,const unsigned char *sig,size_t siglen){
    if(!opaque||!msg||!sig||siglen<617||siglen>667||sig[0]!=0x39)return 0;
    FalconState *s=opaque;
    return falcon_neon_verify(sig,siglen,FALCON_SIG_COMPRESSED,s->public_key,sizeof s->public_key,msg,msglen,s->tmp.bytes,sizeof s->tmp.bytes)==0;
}
/* Public-only interoperability helper: accepts TRON's untagged h body. */
int falcon_verify_raw(const unsigned char *pk,size_t pklen,const unsigned char *msg,size_t msglen,const unsigned char *sig,size_t siglen){
    if(!pk||pklen!=896||!msg||!sig||siglen<617||siglen>667||sig[0]!=0x39)return 0;
    unsigned char tagged[897];tagged[0]=0x09;memcpy(tagged+1,pk,896);
    union {uint64_t align;unsigned char bytes[FALCON_TMPSIZE_VERIFY(9)];}tmp;
    return falcon_neon_verify(sig,siglen,FALCON_SIG_COMPRESSED,tagged,sizeof tagged,msg,msglen,tmp.bytes,sizeof tmp.bytes)==0;
}
/* Object payload, including encoded keys and retained scratch, not RSS or stack peak. */
size_t falcon_state_bytes(void){return sizeof(FalconState);}

#ifdef FALCON_WRAPPER_TEST_MAIN
#include <stdio.h>
static void must(int ok,const char *s){if(!ok){fprintf(stderr,"FAIL %s\n",s);exit(1);}}
static unsigned char *fromhex(const char *s,size_t *n){*n=strlen(s)/2;unsigned char *b=malloc(*n+1);must(b!=NULL,"allocation");for(size_t i=0;i<*n;i++){unsigned v;must(sscanf(s+2*i,"%2x",&v)==1,"hex");b[i]=(unsigned char)v;}return b;}
int main(int argc,char **argv){
    must(argc==2,"public KAT TSV required");FILE *f=fopen(argv[1],"r");must(f!=NULL,"KAT file");char *line=NULL;size_t cap=0;int kat=0;
    while(getline(&line,&cap,f)>0){char *save=NULL,*a=strtok_r(line,"\t\n",&save),*b=strtok_r(NULL,"\t\n",&save),*c=strtok_r(NULL,"\t\n",&save);must(a&&b&&c,"KAT fields");size_t pl,ml,sl;unsigned char *pk=fromhex(a,&pl),*msg=fromhex(b,&ml),*sig=fromhex(c,&sl);must(falcon_verify_raw(pk,pl,msg,ml,sig,sl),"official BC public KAT");msg[0]^=1;must(!falcon_verify_raw(pk,pl,msg,ml,sig,sl),"KAT altered message");msg[0]^=1;sig[sl-1]^=1;must(!falcon_verify_raw(pk,pl,msg,ml,sig,sl),"KAT altered signature");free(pk);free(msg);free(sig);kat++;}
    free(line);fclose(f);must(kat==2,"two KATs");
    unsigned char pk[896],pk2[896],msg[32]={0},sig[752];size_t pl=sizeof pk,pl2=sizeof pk2;void *state=falcon_new(pk,&pl),*other=falcon_new(pk2,&pl2);must(state&&other,"fresh random keygen");must(memcmp(pk,pk2,896)!=0,"random keygen differs");
    size_t lo=1000,hi=0;
    for(int i=0;i<32;i++){msg[0]=(unsigned char)i;size_t sl=sizeof sig;must(falcon_sign(state,msg,sizeof msg,sig,&sl),"sign");if(sl<lo)lo=sl;if(sl>hi)hi=sl;must(falcon_verify(state,msg,sizeof msg,sig,sl),"verify");must(falcon_verify_raw(pk,pl,msg,sizeof msg,sig,sl),"public raw verify");must(!falcon_verify(other,msg,sizeof msg,sig,sl),"wrong key");msg[31]^=1;must(!falcon_verify(state,msg,sizeof msg,sig,sl),"changed message");msg[31]^=1;sig[sl-1]^=1;must(!falcon_verify(state,msg,sizeof msg,sig,sl),"changed signature");sig[sl-1]^=1;must(!falcon_verify(state,msg,sizeof msg,sig,sl-1),"truncated signature");}
    printf("PASS: 2 BC official Falcon KATs + altered-message/signature negatives; 32 fresh-random C sign/verify vectors + four negatives; TRON pk=896 sk=1280 sig=%zu..%zu; state_bytes=%zu\n",lo,hi,falcon_state_bytes());
    falcon_free(state);falcon_free(other);return 0;
}
#endif
