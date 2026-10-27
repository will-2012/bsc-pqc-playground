#include <stddef.h>
int pq_keygen(unsigned char *pk, unsigned char *sk);
int pq_sign(const unsigned char *sk, const unsigned char *msg, size_t n, unsigned char *sig, size_t *len);
int pq_verify(const unsigned char *pk, const unsigned char *msg, size_t n, const unsigned char *sig, size_t len);
