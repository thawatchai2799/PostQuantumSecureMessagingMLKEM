/* kem_glue.h - ML-KEM-512 for the benchmark, through mlkem-native's C backend
 * (third_party/mlkem-native, the version vendored by liboqs 0.16.0). */
#ifndef KEM_GLUE_H
#define KEM_GLUE_H
#include <stddef.h>
#include <stdint.h>

#define KEM_PK_BYTES 800
#define KEM_SK_BYTES 1632
#define KEM_CT_BYTES 768
#define KEM_SS_BYTES 32

/* Randomized API, as a node would use it. randombytes() is a deterministic
 * SHAKE256 stream in this benchmark: only timing is measured, no key is used. */
int kem_keypair(uint8_t *pk, uint8_t *sk);
int kem_encaps(uint8_t *ct, uint8_t *ss, const uint8_t *pk);
int kem_decaps(uint8_t *ss, const uint8_t *ct, const uint8_t *sk);

/* Derandomized key exchange from two seed strings: coins_keypair =
 * SHAKE256(kp_seed, 64), coins_encaps = SHAKE256(enc_seed, 32). Writes
 * SHA3-256(pk || ct || ss) to digest[32]. Returns 1 if keypair, encaps and
 * decaps all succeeded and both shared secrets agree, else 0. The host
 * program make_kem_vector runs the same function (and cross-checks it
 * against liboqs) to produce the expected digest in kem_vector.h. */
int kem_derand_digest(const char *kp_seed, const char *enc_seed, uint8_t digest[32]);

/* Exposed for make_kem_vector's cross-check against liboqs. */
int kem_derand_raw(const char *kp_seed, const char *enc_seed,
                   uint8_t pk[KEM_PK_BYTES], uint8_t sk[KEM_SK_BYTES],
                   uint8_t ct[KEM_CT_BYTES], uint8_t ss[KEM_SS_BYTES],
                   uint8_t coins_kp[64], uint8_t coins_enc[32]);
#endif
