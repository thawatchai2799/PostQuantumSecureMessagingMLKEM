/* bench_core.c - on-device benchmark of the encoding schemes and ML-KEM-512.
 *
 * Platform-independent part of m4bench. For every (corpus, length, scheme)
 * cell it copies the input into RAM, then REPS times: paints the stack,
 * times one encode call and one decode call with the platform cycle
 * counter, records the stack high-water mark of each call, checks the
 * round trip and compares the encoded size with the size the host pipeline
 * produced for the same input (m4_data.h). ML-KEM-512 (mlkem-native, C backend)
 * key generation, encapsulation and decapsulation are timed the same way,
 * and a derandomized key exchange is checked against a digest computed on
 * the host from the same implementation and cross-checked against liboqs
 * (kem_vector.h, written by make_kem_vector).
 *
 * Output: CSV text, one record per line, terminated by "end".
 *   info,<field>,<value>
 *   null,<rep>,<cycles>,<stack>                     empty call: overheads
 *   cell,<corpus>,<chars>,<scheme>,<rep>,<enc_cycles>,<enc_stack>,
 *        <dec_cycles>,<dec_stack>,<enc_bytes>,<expected_bytes>,<ok>
 *   kem,<rep>,<keygen_cycles>,<keygen_stack>,<encaps_cycles>,<encaps_stack>,
 *        <decaps_cycles>,<decaps_stack>,<ok>
 *   kem_check,<digest>,<ok>
 *   end,<cells_ok>,<cells_failed>
 * <ok> is 1 when every check of the record passed, else 0.
 */
#include <stdint.h>
#include <string.h>

#include "plat.h"
#include "m4_data.h"
#include "kem_vector.h"
#include "../codec.h"
#include "heatshrink_encoder.h"
#include "heatshrink_decoder.h"
#include "unishox2.h"
#include "kem_glue.h"

#ifndef REPS
#define REPS 5
#endif
#ifndef OPT_NAME
#define OPT_NAME "?"
#endif
#define MAXOUT (2 * M4_MAXBYTES + 64)

static uint8_t g_in[M4_MAXBYTES];
static uint8_t g_out[MAXOUT];
static uint8_t g_back[MAXOUT];
static uint8_t g_ref[M4_MAXBYTES];          /* S1 canonical form of the input */

typedef long (*codec_fn)(const uint8_t *in, size_t n, uint8_t *out);

/* ---------------- codec wrappers (uniform signature) ---------------- */
static s2_table_id g_s2id;

static long w_s1_enc(const uint8_t *in, size_t n, uint8_t *out) { return s1_encode(in, n, out); }
static long w_s1_dec(const uint8_t *in, size_t n, uint8_t *out) { return s1_decode(in, n, out); }
static long w_s2_enc(const uint8_t *in, size_t n, uint8_t *out) { return s2_encode_id(g_s2id, in, n, out); }
static long w_s2_dec_bit(const uint8_t *in, size_t n, uint8_t *out) { return s2_decode_id(g_s2id, in, n, out); }
static long w_s2_dec_lut(const uint8_t *in, size_t n, uint8_t *out) { return s2_decode_lut_id(g_s2id, in, n, out); }
static long w_s4(const uint8_t *in, size_t n, uint8_t *out) { memcpy(out, in, n); return (long)n; }
static long w_ux_enc(const uint8_t *in, size_t n, uint8_t *out) {
    return unishox2_compress_simple((const char *)in, (int)n, (char *)out);
}
static long w_ux_dec(const uint8_t *in, size_t n, uint8_t *out) {
    return unishox2_decompress_simple((const char *)in, (int)n, (char *)out);
}

/* heatshrink: coder state allocated once, outside the timed region, and
 * reset per call, exactly as the host driver bench_lib.c does. */
static heatshrink_encoder *g_hse8, *g_hse11;
static heatshrink_decoder *g_hsd8, *g_hsd11;
static heatshrink_encoder *g_hse;
static heatshrink_decoder *g_hsd;

static long hs_enc(const uint8_t *in, size_t n, uint8_t *out) {
    heatshrink_encoder_reset(g_hse);
    size_t pos = 0, o = 0, k;
    while (pos < n) {
        if (heatshrink_encoder_sink(g_hse, (uint8_t *)in + pos, n - pos, &k) < 0) return -1;
        pos += k;
        HSE_poll_res pr;
        do {
            pr = heatshrink_encoder_poll(g_hse, out + o, MAXOUT - o, &k);
            if (pr < 0) return -1;
            o += k;
        } while (pr == HSER_POLL_MORE);
    }
    while (heatshrink_encoder_finish(g_hse) == HSER_FINISH_MORE) {
        HSE_poll_res pr;
        do {
            pr = heatshrink_encoder_poll(g_hse, out + o, MAXOUT - o, &k);
            if (pr < 0) return -1;
            o += k;
        } while (pr == HSER_POLL_MORE);
    }
    return (long)o;
}

static long hs_dec(const uint8_t *in, size_t n, uint8_t *out) {
    heatshrink_decoder_reset(g_hsd);
    size_t pos = 0, o = 0, k;
    while (pos < n) {
        if (heatshrink_decoder_sink(g_hsd, (uint8_t *)in + pos, n - pos, &k) < 0) return -1;
        pos += k;
        HSD_poll_res pr;
        do {
            pr = heatshrink_decoder_poll(g_hsd, out + o, MAXOUT - o, &k);
            if (pr < 0) return -1;
            o += k;
        } while (pr == HSDR_POLL_MORE);
    }
    while (heatshrink_decoder_finish(g_hsd) == HSDR_FINISH_MORE) {
        HSD_poll_res pr;
        do {
            pr = heatshrink_decoder_poll(g_hsd, out + o, MAXOUT - o, &k);
            if (pr < 0) return -1;
            o += k;
        } while (pr == HSDR_POLL_MORE);
    }
    return (long)o;
}

static long w_hs8_enc(const uint8_t *in, size_t n, uint8_t *out) { g_hse = g_hse8; return hs_enc(in, n, out); }
static long w_hs8_dec(const uint8_t *in, size_t n, uint8_t *out) { g_hsd = g_hsd8; return hs_dec(in, n, out); }
static long w_hs11_enc(const uint8_t *in, size_t n, uint8_t *out) { g_hse = g_hse11; return hs_enc(in, n, out); }
static long w_hs11_dec(const uint8_t *in, size_t n, uint8_t *out) { g_hsd = g_hsd11; return hs_dec(in, n, out); }

static long w_null(const uint8_t *in, size_t n, uint8_t *out) { (void)in; (void)n; (void)out; return 0; }

/* ---------------- one timed call ---------------- */
static long timed(codec_fn f, const uint8_t *in, size_t n, uint8_t *out,
                  uint32_t *cycles, uint32_t *stack) {
    uint32_t sp0 = plat_sp();
    plat_stack_paint(sp0);
    uint32_t c0 = plat_cycles();
    long r = f(in, n, out);
    uint32_t c1 = plat_cycles();
    *cycles = c1 - c0;
    *stack = plat_stack_used(sp0);
    return r;
}

/* S1 is lossy by design: its decoder returns the uppercase-folded input
 * with out-of-alphabet characters removed. This is the reference the
 * round-trip check uses for S1 (same rule as the host harness). */
static const char S1_ALPHABET[55] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ .?!,-/:=()@#$%&+*0123456789";
static size_t s1_canonical(const uint8_t *in, size_t n, uint8_t *out) {
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        uint8_t c = in[i];
        if (c >= 'a' && c <= 'z') c -= 32;
        if (memchr(S1_ALPHABET, c, 54)) out[o++] = c;
    }
    return o;
}

/* ---------------- output helpers ---------------- */
static void put_field_u(uint32_t v) { plat_puts(","); plat_putu(v); }
static void put_field_s(const char *s) { plat_puts(","); plat_puts(s); }

/* scheme table: index matches M4_EXPECTED's third dimension where noted */
typedef struct {
    const char *name;     /* as in the paper's CSV files */
    int expected_index;   /* column in M4_EXPECTED, -1 = expected equals input bytes */
    codec_fn enc, dec;
    int s1_reference;     /* round trip against the S1 canonical form */
} scheme_t;

static const scheme_t SCHEMES[] = {
    {"S1-6bit",      0, w_s1_enc,   w_s1_dec,     1},
    {"S2-dom-bit",   1, w_s2_enc,   w_s2_dec_bit, 0},
    {"S2-dom-table", 1, w_s2_enc,   w_s2_dec_lut, 0},
    {"S4-utf8",     -1, w_s4,       w_s4,         0},
    {"S5-hs8",       2, w_hs8_enc,  w_hs8_dec,    0},
    {"S5-hs11",      3, w_hs11_enc, w_hs11_dec,   0},
    {"S6-unishox",   4, w_ux_enc,   w_ux_dec,     0},
};
#define NSCHEMES ((int)(sizeof SCHEMES / sizeof SCHEMES[0]))

static uint32_t g_ok, g_fail;

static void run_cell(int ci, int li) {
    size_t nbytes = M4_INPUT_BYTES[ci][li];
    memcpy(g_in, M4_INPUT[ci][li], nbytes);
    size_t nref = s1_canonical(g_in, nbytes, g_ref);
    g_s2id = ci == 0 ? S2_TABLE_EN : ci == 1 ? S2_TABLE_C2 : S2_TABLE_C3;

    for (int si = 0; si < NSCHEMES; si++) {
        const scheme_t *s = &SCHEMES[si];
        int32_t expected = s->expected_index < 0 ? (int32_t)nbytes
                                                 : M4_EXPECTED[ci][li][s->expected_index];
        if (expected < 0) continue;              /* cell not measured on the host (S1 on C2) */
        for (int rep = 0; rep < REPS; rep++) {
            uint32_t ce, se, cd, sd;
            memset(g_out, 0, MAXOUT);
            memset(g_back, 0, MAXOUT);
            long m = timed(s->enc, g_in, nbytes, g_out, &ce, &se);
            long back = m < 0 ? -1 : timed(s->dec, g_out, (size_t)m, g_back, &cd, &sd);
            int ok = m >= 0 && back >= 0 && m == expected;
            if (ok) {
                if (s->s1_reference)
                    ok = (size_t)back == nref && memcmp(g_back, g_ref, nref) == 0;
                else
                    ok = (size_t)back == nbytes && memcmp(g_back, g_in, nbytes) == 0;
            }
            if (m < 0) { cd = 0; sd = 0; }
            plat_puts("cell");
            put_field_s(M4_CORPUS_NAME[ci]);
            put_field_u(M4_LENGTHS[li]);
            put_field_s(s->name);
            put_field_u((uint32_t)rep);
            put_field_u(ce); put_field_u(se);
            put_field_u(cd); put_field_u(sd);
            put_field_u(m < 0 ? 0u : (uint32_t)m);
            put_field_u((uint32_t)expected);
            put_field_u(ok ? 1u : 0u);
            plat_puts("\n");
            if (ok) g_ok++; else g_fail++;
        }
    }
}

/* ---------------- ML-KEM-512 ---------------- */
static uint8_t k_pk[KEM_PK_BYTES], k_sk[KEM_SK_BYTES], k_ct[KEM_CT_BYTES];
static uint8_t k_ss1[KEM_SS_BYTES], k_ss2[KEM_SS_BYTES];

static long w_kem_keypair(const uint8_t *in, size_t n, uint8_t *out) {
    (void)in; (void)n; (void)out;
    return kem_keypair(k_pk, k_sk);
}
static long w_kem_encaps(const uint8_t *in, size_t n, uint8_t *out) {
    (void)in; (void)n; (void)out;
    return kem_encaps(k_ct, k_ss1, k_pk);
}
static long w_kem_decaps(const uint8_t *in, size_t n, uint8_t *out) {
    (void)in; (void)n; (void)out;
    return kem_decaps(k_ss2, k_ct, k_sk);
}

static void run_kem(void) {
    for (int rep = 0; rep < REPS; rep++) {
        uint32_t c[3], s[3];
        long r0 = timed(w_kem_keypair, NULL, 0, NULL, &c[0], &s[0]);
        long r1 = timed(w_kem_encaps, NULL, 0, NULL, &c[1], &s[1]);
        long r2 = timed(w_kem_decaps, NULL, 0, NULL, &c[2], &s[2]);
        int ok = r0 == 0 && r1 == 0 && r2 == 0 && memcmp(k_ss1, k_ss2, sizeof k_ss1) == 0;
        plat_puts("kem");
        put_field_u((uint32_t)rep);
        for (int i = 0; i < 3; i++) { put_field_u(c[i]); put_field_u(s[i]); }
        put_field_u(ok ? 1u : 0u);
        plat_puts("\n");
        if (ok) g_ok++; else g_fail++;
    }
    /* Derandomized check: the same seeds as the host program make_kem_vector,
     * so the digest of pk || ct || ss must equal KEM_VECTOR_DIGEST. */
    uint8_t digest[32];
    int ok = kem_derand_digest(KEM_VECTOR_KP_SEED, KEM_VECTOR_ENC_SEED, digest);
    ok = ok && memcmp(digest, KEM_VECTOR_DIGEST, 32) == 0;
    plat_puts("kem_check,");
    plat_puthex(digest, 32);
    put_field_u(ok ? 1u : 0u);
    plat_puts("\n");
    if (ok) g_ok++; else g_fail++;
}

/* ---------------- entry ---------------- */
int bench_main(void) {
    plat_init();
    codec_init();
    uint32_t h0 = plat_heap_used();
    g_hse8 = heatshrink_encoder_alloc(8, 4);
    uint32_t h1 = plat_heap_used();
    g_hsd8 = heatshrink_decoder_alloc(256, 8, 4);
    uint32_t h2 = plat_heap_used();
    g_hse11 = heatshrink_encoder_alloc(11, 4);
    uint32_t h3 = plat_heap_used();
    g_hsd11 = heatshrink_decoder_alloc(256, 11, 4);
    uint32_t h4 = plat_heap_used();
    if (!g_hse8 || !g_hse11 || !g_hsd8 || !g_hsd11) {
        plat_puts("fatal,heatshrink allocation failed\n");
        return 1;
    }
    for (;;) {
        if (plat_wait_start()) return 0;
        g_ok = g_fail = 0;
        plat_puts("info,platform,"); plat_puts(plat_name()); plat_puts("\n");
        plat_puts("info,cpu_hz,"); plat_putu(plat_cpu_hz()); plat_puts("\n");
        plat_puts("info,reps,"); plat_putu(REPS); plat_puts("\n");
        plat_puts("info,kem,mlkem-native ML-KEM-512 C backend\n");
        plat_puts("info,opt,"); plat_puts(OPT_NAME); plat_puts("\n");
        plat_puts("info,static_bytes,"); plat_putu(plat_static_bytes()); plat_puts("\n");
        plat_puts("info,hs8_encoder_heap,"); plat_putu(h1 - h0); plat_puts("\n");
        plat_puts("info,hs8_decoder_heap,"); plat_putu(h2 - h1); plat_puts("\n");
        plat_puts("info,hs11_encoder_heap,"); plat_putu(h3 - h2); plat_puts("\n");
        plat_puts("info,hs11_decoder_heap,"); plat_putu(h4 - h3); plat_puts("\n");
        for (int rep = 0; rep < REPS; rep++) {
            uint32_t c, s;
            timed(w_null, g_in, 0, g_out, &c, &s);
            plat_puts("null"); put_field_u((uint32_t)rep); put_field_u(c); put_field_u(s); plat_puts("\n");
        }
        for (int ci = 0; ci < M4_NCORPORA; ci++)
            for (int li = 0; li < M4_NLENGTHS; li++)
                run_cell(ci, li);
        run_kem();
        plat_puts("end"); put_field_u(g_ok); put_field_u(g_fail); plat_puts("\n");
    }
}
