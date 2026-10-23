/* bench_kem.c - ML-KEM session costs, timed in C through the liboqs C API.
 *
 * Times key generation, encapsulation and decapsulation of one ML-KEM
 * parameter set with the same clock and protocol as the codec and AEAD
 * drivers (clock_gettime(CLOCK_MONOTONIC) around one call, untimed warm-up,
 * one row per repetition), so that the per-session KEM cost is measured on
 * the same footing as the per-message costs.
 *
 * The OQS_KEM object and all buffers are allocated once outside the timed
 * region. Each repetition generates a fresh key pair, encapsulates to it and
 * decapsulates the ciphertext; the two shared secrets are compared after the
 * timed calls, and the key and ciphertext lengths are checked against
 * FIPS 203.
 *
 * Usage:  bench_kem <ML-KEM-512|ML-KEM-768|ML-KEM-1024> <reps> [warmup]
 * Output: "op,rep,ns" rows for keygen, encaps and decaps, preceded by one
 *         "info" line: liboqs version, public key bytes, ciphertext bytes.
 * Exit:   0 on success, 3 if the parameter set is not enabled in the
 *         linked liboqs build, 1 on any failure.
 * Build:  gcc -O2 -o bench_kem bench_kem.c -I<liboqs>/include \
 *             <liboqs>/lib/liboqs.so -Wl,-rpath,<liboqs>/lib
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <oqs/oqs.h>

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static const struct { const char *name; size_t pk, ct; } FIPS203[] = {
    {"ML-KEM-512", 800, 768}, {"ML-KEM-768", 1184, 1088}, {"ML-KEM-1024", 1568, 1568},
};

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: bench_kem <param set> <reps> [warmup]\n"); return 2; }
    const char *alg = argv[1];
    int reps = atoi(argv[2]), warmup = argc > 3 ? atoi(argv[3]) : 5;
    size_t want_pk = 0, want_ct = 0;
    for (size_t i = 0; i < sizeof FIPS203 / sizeof FIPS203[0]; i++)
        if (!strcmp(alg, FIPS203[i].name)) { want_pk = FIPS203[i].pk; want_ct = FIPS203[i].ct; }
    if (!want_pk || reps < 1 || warmup < 0) { fprintf(stderr, "bad arguments\n"); return 2; }

    OQS_init();
    OQS_KEM *kem = OQS_KEM_new(alg);
    if (!kem) { fprintf(stderr, "%s is not enabled in this liboqs build\n", alg); return 3; }
    if (kem->length_public_key != want_pk || kem->length_ciphertext != want_ct) {
        fprintf(stderr, "%s: sizes (%zu, %zu) differ from FIPS 203 (%zu, %zu)\n", alg,
                kem->length_public_key, kem->length_ciphertext, want_pk, want_ct);
        return 1;
    }
    uint8_t *pk = malloc(kem->length_public_key), *sk = malloc(kem->length_secret_key);
    uint8_t *ct = malloc(kem->length_ciphertext);
    uint8_t *ss1 = malloc(kem->length_shared_secret), *ss2 = malloc(kem->length_shared_secret);
    if (!pk || !sk || !ct || !ss1 || !ss2) { fprintf(stderr, "allocation failed\n"); return 1; }

    for (int r = 0; r < warmup; r++) {
        if (OQS_KEM_keypair(kem, pk, sk) != OQS_SUCCESS ||
            OQS_KEM_encaps(kem, ct, ss1, pk) != OQS_SUCCESS ||
            OQS_KEM_decaps(kem, ss2, ct, sk) != OQS_SUCCESS ||
            memcmp(ss1, ss2, kem->length_shared_secret)) { fprintf(stderr, "warm-up failed\n"); return 1; }
    }
    printf("info,%s,%zu,%zu\n", OQS_version(), kem->length_public_key, kem->length_ciphertext);
    printf("op,rep,ns\n");
    for (int r = 0; r < reps; r++) {
        uint64_t t0 = now_ns();
        OQS_STATUS a = OQS_KEM_keypair(kem, pk, sk);
        uint64_t t1 = now_ns();
        OQS_STATUS b = OQS_KEM_encaps(kem, ct, ss1, pk);
        uint64_t t2 = now_ns();
        OQS_STATUS c = OQS_KEM_decaps(kem, ss2, ct, sk);
        uint64_t t3 = now_ns();
        if (a != OQS_SUCCESS || b != OQS_SUCCESS || c != OQS_SUCCESS ||
            memcmp(ss1, ss2, kem->length_shared_secret)) { fprintf(stderr, "KEM failed at rep %d\n", r); return 1; }
        printf("keygen,%d,%llu\n", r, (unsigned long long)(t1 - t0));
        printf("encaps,%d,%llu\n", r, (unsigned long long)(t2 - t1));
        printf("decaps,%d,%llu\n", r, (unsigned long long)(t3 - t2));
    }
    OQS_MEM_secure_free(sk, kem->length_secret_key);
    free(pk); free(ct); free(ss1); free(ss2);
    OQS_KEM_free(kem);
    OQS_destroy();
    return 0;
}
