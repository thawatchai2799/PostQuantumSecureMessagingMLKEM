/* kem_glue.c - see kem_glue.h. Compiled with -DMLK_CONFIG_PARAMETER_SET=512
 * and -I third_party/mlkem-native/mlkem, like mlkem_native.c itself. */
#include <string.h>

#include "mlkem_native.h"
#include "src/fips202/fips202.h"
#include "src/randombytes.h"
#include "kem_glue.h"

/* Deterministic randombytes(): a SHAKE256 stream over a fixed seed and a
 * counter. The benchmark measures time and stack only; nothing derived from
 * these bytes is ever used as a key. */
static uint32_t g_rb_counter;

int randombytes(uint8_t *out, size_t outlen) {
    uint8_t seed[16] = {'m', '4', 'b', 'e', 'n', 'c', 'h', ' ', 'r', 'n', 'g', ' ', 0, 0, 0, 0};
    seed[12] = (uint8_t)(g_rb_counter >> 24);
    seed[13] = (uint8_t)(g_rb_counter >> 16);
    seed[14] = (uint8_t)(g_rb_counter >> 8);
    seed[15] = (uint8_t)(g_rb_counter);
    g_rb_counter++;
    mlk_shake256(out, outlen, seed, sizeof seed);
    return 0;
}

int kem_keypair(uint8_t *pk, uint8_t *sk) { return crypto_kem_keypair(pk, sk); }
int kem_encaps(uint8_t *ct, uint8_t *ss, const uint8_t *pk) { return crypto_kem_enc(ct, ss, pk); }
int kem_decaps(uint8_t *ss, const uint8_t *ct, const uint8_t *sk) { return crypto_kem_dec(ss, ct, sk); }

int kem_derand_raw(const char *kp_seed, const char *enc_seed,
                   uint8_t pk[KEM_PK_BYTES], uint8_t sk[KEM_SK_BYTES],
                   uint8_t ct[KEM_CT_BYTES], uint8_t ss[KEM_SS_BYTES],
                   uint8_t coins_kp[64], uint8_t coins_enc[32]) {
    uint8_t ss2[KEM_SS_BYTES];
    mlk_shake256(coins_kp, 64, (const uint8_t *)kp_seed, strlen(kp_seed));
    mlk_shake256(coins_enc, 32, (const uint8_t *)enc_seed, strlen(enc_seed));
    if (crypto_kem_keypair_derand(pk, sk, coins_kp) != 0) return 0;
    if (crypto_kem_enc_derand(ct, ss, pk, coins_enc) != 0) return 0;
    if (crypto_kem_dec(ss2, ct, sk) != 0) return 0;
    return memcmp(ss, ss2, KEM_SS_BYTES) == 0;
}

int kem_derand_digest(const char *kp_seed, const char *enc_seed, uint8_t digest[32]) {
    static uint8_t pk[KEM_PK_BYTES], sk[KEM_SK_BYTES], ct[KEM_CT_BYTES];
    static uint8_t buf[KEM_PK_BYTES + KEM_CT_BYTES + KEM_SS_BYTES];
    uint8_t ss[KEM_SS_BYTES], coins_kp[64], coins_enc[32];
    int ok = kem_derand_raw(kp_seed, enc_seed, pk, sk, ct, ss, coins_kp, coins_enc);
    memcpy(buf, pk, KEM_PK_BYTES);
    memcpy(buf + KEM_PK_BYTES, ct, KEM_CT_BYTES);
    memcpy(buf + KEM_PK_BYTES + KEM_CT_BYTES, ss, KEM_SS_BYTES);
    mlk_sha3_256(digest, buf, sizeof buf);
    return ok;
}
