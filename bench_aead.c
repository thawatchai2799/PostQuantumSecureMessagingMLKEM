/* bench_aead.c - AES-256-GCM cost per message, timed in C.
 *
 * The DEM of the pipeline (AES-256-GCM, 96-bit nonce, 16-byte tag) is timed
 * here with the same clock and protocol as the codec drivers, for payloads
 * of the exact sizes the encoders produce, so that the AEAD share of the
 * per-message cost can be compared with the codec cost. AEAD time depends
 * on the payload length only, not on its content, so a deterministic
 * pseudo-random buffer of each length is used.
 *
 * The OpenSSL EVP context and key are set up once outside the timed region;
 * each timed call sets a fresh nonce, processes the payload and produces
 * (encrypt) or checks (decrypt) the tag, as a node would per message.
 *
 * Usage:  bench_aead <payload bytes> <reps>
 * Output: "op,rep,ns" rows for encrypt and decrypt.
 * Build:  gcc -O2 -o bench_aead bench_aead.c -lcrypto
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#include <openssl/evp.h>

#define MAXP (1u << 20)
static unsigned char g_pt[MAXP], g_ct[MAXP + 16], g_back[MAXP + 16];

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static EVP_CIPHER_CTX *g_enc, *g_dec;
static unsigned char g_key[32], g_nonce[12], g_tag[16];

static int seal(int n, uint32_t counter) {
    int len = 0, fin = 0;
    memcpy(g_nonce + 8, &counter, 4);
    if (!EVP_EncryptInit_ex(g_enc, NULL, NULL, NULL, g_nonce)) return -1;
    if (!EVP_EncryptUpdate(g_enc, g_ct, &len, g_pt, n)) return -1;
    if (!EVP_EncryptFinal_ex(g_enc, g_ct + len, &fin)) return -1;
    if (!EVP_CIPHER_CTX_ctrl(g_enc, EVP_CTRL_GCM_GET_TAG, 16, g_tag)) return -1;
    return len + fin;
}

static int open_(int n) {
    int len = 0, fin = 0;
    if (!EVP_DecryptInit_ex(g_dec, NULL, NULL, NULL, g_nonce)) return -1;
    if (!EVP_DecryptUpdate(g_dec, g_back, &len, g_ct, n)) return -1;
    if (!EVP_CIPHER_CTX_ctrl(g_dec, EVP_CTRL_GCM_SET_TAG, 16, g_tag)) return -1;
    if (EVP_DecryptFinal_ex(g_dec, g_back + len, &fin) <= 0) return -1;   /* tag check */
    return len + fin;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: bench_aead <payload bytes> <reps>\n"); return 2; }
    int n = atoi(argv[1]), reps = atoi(argv[2]);
    if (n < 0 || (unsigned)n > MAXP) { fprintf(stderr, "bad size\n"); return 2; }
    uint32_t x = 2463534242u;                         /* xorshift32: deterministic buffer */
    for (int i = 0; i < n; i++) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; g_pt[i] = (unsigned char)x; }
    for (int i = 0; i < 32; i++) g_key[i] = (unsigned char)(i * 7 + 1);
    g_enc = EVP_CIPHER_CTX_new(); g_dec = EVP_CIPHER_CTX_new();
    if (!g_enc || !g_dec) return 1;
    if (!EVP_EncryptInit_ex(g_enc, EVP_aes_256_gcm(), NULL, g_key, NULL)) return 1;
    if (!EVP_DecryptInit_ex(g_dec, EVP_aes_256_gcm(), NULL, g_key, NULL)) return 1;
    if (!EVP_CIPHER_CTX_ctrl(g_enc, EVP_CTRL_GCM_SET_IVLEN, 12, NULL)) return 1;
    if (!EVP_CIPHER_CTX_ctrl(g_dec, EVP_CTRL_GCM_SET_IVLEN, 12, NULL)) return 1;
    uint32_t ctr = 0;
    for (int r = 0; r < 5; r++) { if (seal(n, ctr++) != n || open_(n) != n) { fprintf(stderr, "aead failed\n"); return 1; } }
    if (memcmp(g_back, g_pt, (size_t)n)) { fprintf(stderr, "round trip mismatch\n"); return 1; }
    printf("op,rep,ns\n");
    for (int r = 0; r < reps; r++) {
        uint64_t a = now_ns();
        int e = seal(n, ctr++);
        uint64_t b = now_ns();
        int d = open_(n);
        uint64_t c = now_ns();
        if (e != n || d != n) { fprintf(stderr, "aead failed\n"); return 1; }
        printf("encrypt,%d,%llu\n", r, (unsigned long long)(b - a));
        printf("decrypt,%d,%llu\n", r, (unsigned long long)(c - b));
    }
    EVP_CIPHER_CTX_free(g_enc); EVP_CIPHER_CTX_free(g_dec);
    return 0;
}
