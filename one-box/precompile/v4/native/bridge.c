#include "bridge.h"
#include <errno.h>
#include <unistd.h>
#include <sys/random.h>
#define MLD_CONFIG_PARAMETER_SET 44
#define MLD_CONFIG_NAMESPACE_PREFIX PQCP_MLDSA_NATIVE_MLDSA44
#include "mldsa_native.h"
int randombytes(uint8_t *out,size_t length){while(length){size_t n=length>256?256:length;int rc;do{rc=getentropy(out,n);}while(rc!=0&&errno==EINTR);if(rc)return -1;out+=n;length-=n;}return 0;}
int pq_ml_keygen(unsigned char *pk,unsigned char *sk){return PQCP_MLDSA_NATIVE_MLDSA44_keypair(pk,sk);}
int pq_ml_sign(const unsigned char *sk,const unsigned char *m,size_t n,unsigned char *sig){return PQCP_MLDSA_NATIVE_MLDSA44_signature(sig,m,n,NULL,0,sk);}
int pq_ml_verify(const unsigned char *pk,const unsigned char *m,size_t n,const unsigned char *sig){return PQCP_MLDSA_NATIVE_MLDSA44_verify(sig,m,n,NULL,0,pk);}
