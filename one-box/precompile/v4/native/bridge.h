#include <stddef.h>
int pq_ml_keygen(unsigned char*,unsigned char*);
int pq_ml_sign(const unsigned char*,const unsigned char*,size_t,unsigned char*);
int pq_ml_verify(const unsigned char*,const unsigned char*,size_t,const unsigned char*);
