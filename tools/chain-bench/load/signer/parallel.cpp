// Bench-only Pure SLH-DSA-SHA2-128s; same pipe protocol as slh.c.
// SIMD parallelizes independent hashes; each signer uses one OS thread.
#include "api.h"
#include <cerrno>
#include <cstdio>
#include <sys/random.h>
#include <sys/resource.h>

static void erase(unsigned char *p, size_t size) {
    volatile unsigned char *v = p;
    while (size--) *v++ = 0;
}

static bool entropy(void *target, size_t size) {
    auto *p = static_cast<unsigned char *>(target);
    while (size) {
        ssize_t count = getrandom(p, size, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        p += count;
        size -= count;
    }
    return true;
}

int main() {
    struct rlimit limit = {0, 0};
    if (setrlimit(RLIMIT_CORE, &limit) != 0 || !__builtin_cpu_supports("avx2")) return 1;
#ifdef BSC_PQC_AVX512
    if (!__builtin_cpu_supports("avx512f")) return 1;
#endif
    unsigned char input[96], signature[7856];
    int result = 0;
    try {
        for (;;) {
            size_t count = fread(input, 1, sizeof(input), stdin);
            if (count == 0 && feof(stdin)) break;
            if (count != sizeof(input)) { result = 1; break; }
            slh_dsa::key_sha2_128s key;
            key.set_num_thread(1);
            key.set_private_key(input);
            bool ok = key.len_signature() == sizeof(signature) &&
                key.sign(signature, sizeof(signature), input + 64, 32, nullptr, 0, entropy) &&
                key.verify(signature, sizeof(signature), input + 64, 32);
            erase(input, sizeof(input));
            if (!ok || fwrite(signature, 1, sizeof(signature), stdout) != sizeof(signature) || fflush(stdout) != 0) {
                result = 1;
                break;
            }
        }
    } catch (...) { result = 1; }
    erase(input, sizeof(input));
    erase(signature, sizeof(signature));
    if (result) fputs("SLH signer failed\n", stderr);
    return result;
}
