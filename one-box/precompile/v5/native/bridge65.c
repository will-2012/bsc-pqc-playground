#include "bridge65.h"
#include <errno.h>
#include <unistd.h>
#include <sys/random.h>
#define MLD_CONFIG_PARAMETER_SET 65
#define MLD_CONFIG_NAMESPACE_PREFIX PQCP_MLDSA_NATIVE_MLDSA65
#include "mldsa_native.h"
/* OS RNG is shared with the ML-DSA-44 bridge in this same package. */
int pq_ml65_keygen(unsigned char *pk,unsigned char *sk){return PQCP_MLDSA_NATIVE_MLDSA65_keypair(pk,sk);}
int pq_ml65_sign(const unsigned char *sk,const unsigned char *m,size_t n,unsigned char *sig){return PQCP_MLDSA_NATIVE_MLDSA65_signature(sig,m,n,NULL,0,sk);}
int pq_ml65_verify(const unsigned char *pk,const unsigned char *m,size_t n,const unsigned char *sig){return PQCP_MLDSA_NATIVE_MLDSA65_verify(sig,m,n,NULL,0,pk);}
