#ifndef CRYPTO_BENCH_NATIVE_H
#define CRYPTO_BENCH_NATIVE_H
#include <stddef.h>
#include <stdint.h>
typedef struct BenchKey BenchKey;
int bench_setup_thread(void);
uint64_t bench_thread_cpu_ns(void);
uint64_t bench_process_cpu_ns(void);
const char *bench_cpu_features(void);
BenchKey *bench_new(int id);
void bench_free(BenchKey *);
size_t bench_public_key(BenchKey *,unsigned char *,size_t);
int bench_sign(BenchKey *,const unsigned char *,size_t,unsigned char *,size_t *);
int bench_verify(BenchKey *,const unsigned char *,size_t,const unsigned char *,size_t);
int bench_cross_verify(BenchKey *,const unsigned char *,size_t,const unsigned char *,size_t);
int bench_sign_into(BenchKey *,const unsigned char *,size_t,unsigned char *,size_t);
int bench_standard_verify(const char *,const unsigned char *,size_t,const unsigned char *,size_t,const unsigned char *,size_t,unsigned char *,size_t);
#endif
