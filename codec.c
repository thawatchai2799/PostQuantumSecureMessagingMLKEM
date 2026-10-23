#include "codec.h"
/*
 * encoders.c — C implementations of S1 (6-bit static) and S2 (canonical
 * Huffman), plus a CLI driver for verification and timing.
 *
 * Design constraints (embedded-friendly, matches the paper's RQ2 framing):
 *   - no heap allocation in encode/decode hot paths (static tables,
 *     caller-provided buffers)
 *   - tables are compile-time constants (s2_tables.h is auto-generated
 *     from the Python-trained tables, so behavior is bit-identical)
 *
 * CLI:
 *   ./encoders enc  <s1|s2en|s2c2|s2c3> <in> <out>     encode file
 *   ./encoders dec  <s1|s2en|s2c2|s2c3> <in> <out>     decode file
 *   ./encoders time <s1|s2en|s2c2|s2c3> <in> <reps>    CSV: op,rep,ns
 *
 * S1 semantics mirror s1_sixbit.py: uppercase-fold, drop out-of-table
 * (Somsuk Alg. 3.1 line 2), EOM = code 63 from the reserved space.
 * S2 semantics mirror s2_huffman.py: UTF-8 bytes + EOF(256), canonical
 * codes reconstructed from lengths by the identical rule.
 */

#include <stdint.h>
#include <string.h>

#include "s2_tables.h"

/* ============================ S1: 6-bit static ======================== */

static const char S1_ALPHABET[55] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ .?!,-/:=()@#$%&+*0123456789";
#define S1_EOM 63

static int8_t s1_char2code[256];

static void s1_init(void) {
    memset(s1_char2code, -1, sizeof s1_char2code);
    for (int i = 0; i < 54; i++)
        s1_char2code[(uint8_t)S1_ALPHABET[i]] = (int8_t)i;
}

/* returns output byte count, or -1 on error */
long s1_encode(const uint8_t *in, size_t n, uint8_t *out) {
    uint32_t acc = 0; int nbits = 0; size_t o = 0;
    for (size_t i = 0; i <= n; i++) {
        int sym;
        if (i == n) sym = S1_EOM;
        else {
            uint8_t c = in[i];
            if (c >= 'a' && c <= 'z') c -= 32;      /* uppercase-fold */
            int8_t code = s1_char2code[c];
            if (code < 0) continue;                  /* drop (Alg 3.1 l.2) */
            sym = code;
        }
        acc = (acc << 6) | (uint32_t)sym; nbits += 6;
        while (nbits >= 8) { nbits -= 8; out[o++] = (uint8_t)(acc >> nbits); }
    }
    if (nbits) out[o++] = (uint8_t)(acc << (8 - nbits));
    return (long)o;
}

long s1_decode(const uint8_t *in, size_t n, uint8_t *out) {
    uint32_t acc = 0; int nbits = 0; size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        acc = (acc << 8) | in[i]; nbits += 8;
        while (nbits >= 6) {
            nbits -= 6;
            uint32_t sym = (acc >> nbits) & 0x3F;
            if (sym == S1_EOM) return (long)o;
            if (sym >= 54) return -1;                /* reserved code */
            out[o++] = (uint8_t)S1_ALPHABET[sym];
        }
    }
    return -1;                                       /* EOM not found */
}

/* ===================== S2: canonical Huffman ========================== */

#define S2_NSYM 257                                  /* bytes + EOF */
#define S2_EOF  256
#define S2_MAXLEN 32
#define S2_LUT_BITS 9    /* 512 entries x 2 bytes = 1 KiB per table */

typedef struct {
    /* encode side */
    uint32_t code[S2_NSYM];
    uint8_t  len[S2_NSYM];
    /* canonical decode side */
    uint32_t first_code[S2_MAXLEN + 1];
    uint16_t count[S2_MAXLEN + 1];
    uint16_t offset[S2_MAXLEN + 1];
    uint16_t sorted[S2_NSYM];                        /* symbols by (len,sym) */
    int      max_len;
    /* table-driven decode side: one S2_LUT_BITS-bit lookup table.
     * entry = (len << 9) | symbol for codes of length <= S2_LUT_BITS;
     * entry = 0 means "longer code: fall back to the canonical walk". */
    uint16_t lut[1u << S2_LUT_BITS];
} s2_table_t;

/* Final, correct version (mirrors Python exactly):
 * items sorted by (len,sym); code=0; prev=len[first];
 * for each item: code <<= (len - prev); prev = len; assign; code++ */
static void s2_build_final(s2_table_t *t, const uint8_t *lens) {
    memset(t, 0, sizeof *t);
    for (int s = 0; s < S2_NSYM; s++) {
        t->len[s] = lens[s];
        t->count[lens[s]]++;
        if (lens[s] > t->max_len) t->max_len = lens[s];
    }
    uint16_t idx = 0;
    for (int L = 1; L <= t->max_len; L++) {
        t->offset[L] = idx;
        for (int s = 0; s < S2_NSYM; s++)
            if (t->len[s] == L) t->sorted[idx++] = (uint16_t)s;
    }
    uint32_t code = 0;
    int prev = 0, first = 1;
    for (int L = 1; L <= t->max_len; L++) {
        for (uint16_t k = 0; k < t->count[L]; k++) {
            uint16_t s = t->sorted[t->offset[L] + k];
            if (first) { prev = L; first = 0; }
            code <<= (L - prev);
            prev = L;
            t->code[s] = code;
            if (k == 0) t->first_code[L] = code;
            code++;
        }
    }
    /* fill the direct lookup table: every prefix of length S2_LUT_BITS that
     * starts with a short code maps to (len, symbol) */
    for (int s = 0; s < S2_NSYM; s++) {
        int L = t->len[s];
        if (L == 0 || L > S2_LUT_BITS) continue;
        uint32_t base = t->code[s] << (S2_LUT_BITS - L);
        uint32_t fill = 1u << (S2_LUT_BITS - L);
        for (uint32_t f = 0; f < fill; f++)
            t->lut[base + f] = (uint16_t)((L << 9) | s);
    }
}

static long s2_encode(const s2_table_t *t, const uint8_t *in, size_t n,
                      uint8_t *out) {
    uint64_t acc = 0; int nbits = 0; size_t o = 0;
    for (size_t i = 0; i <= n; i++) {
        int sym = (i == n) ? S2_EOF : in[i];
        acc = (acc << t->len[sym]) | t->code[sym];
        nbits += t->len[sym];
        while (nbits >= 8) { nbits -= 8; out[o++] = (uint8_t)(acc >> nbits); }
    }
    if (nbits) out[o++] = (uint8_t)(acc << (8 - nbits));
    return (long)o;
}

static long s2_decode(const s2_table_t *t, const uint8_t *in, size_t n,
                      uint8_t *out) {
    uint32_t code = 0; int len = 0; size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        for (int b = 7; b >= 0; b--) {
            code = (code << 1) | ((in[i] >> b) & 1u);
            len++;
            if (len <= t->max_len && t->count[len] &&
                code >= t->first_code[len] &&
                code < t->first_code[len] + t->count[len]) {
                uint16_t s = t->sorted[t->offset[len] +
                                       (code - t->first_code[len])];
                if (s == S2_EOF) return (long)o;
                out[o++] = (uint8_t)s;
                code = 0; len = 0;
            } else if (len > t->max_len) return -1;
        }
    }
    return -1;                                       /* EOF not found */
}

/* Table-driven canonical decoder (the production-style variant).
 * Peeks S2_LUT_BITS bits, resolves (len, symbol) in one lookup and
 * consumes len bits.  Codes longer than S2_LUT_BITS (rare symbols)
 * fall back to the canonical first_code/count walk starting at
 * length S2_LUT_BITS + 1.  Output is byte-identical to s2_decode(). */
static long s2_decode_lut(const s2_table_t *t, const uint8_t *in, size_t n,
                          uint8_t *out) {
    uint64_t acc = 0;        /* bit buffer; only the low nbits are meaningful */
    int nbits = 0; size_t i = 0, o = 0;
    for (;;) {
        if (nbits < 24) {    /* refill rarely (every few symbols), to >= 56 bits */
            while (nbits <= 56 && i < n) { acc = (acc << 8) | in[i++]; nbits += 8; }
            if (nbits <= 0) return -1;               /* EOF not found */
        }
        uint32_t peek;
        if (nbits >= S2_LUT_BITS)
            peek = (uint32_t)(acc >> (nbits - S2_LUT_BITS)) & ((1u << S2_LUT_BITS) - 1);
        else                                         /* tail: zero-pad */
            peek = (uint32_t)((acc & (((uint64_t)1 << nbits) - 1)) << (S2_LUT_BITS - nbits));
        uint16_t e = t->lut[peek];
        int L = e >> 9;
        uint16_t s;
        if (L) {
            if (L > nbits) return -1;                /* truncated stream */
            s = e & 0x1FF;
        } else {                                     /* long code: canonical walk */
            L = S2_LUT_BITS + 1;
            for (;; L++) {
                if (L > t->max_len || L > nbits) return -1;
                uint32_t code = (uint32_t)(acc >> (nbits - L)) & ((1u << L) - 1);
                if (t->count[L] && code >= t->first_code[L] &&
                    code < t->first_code[L] + t->count[L]) {
                    s = t->sorted[t->offset[L] + (code - t->first_code[L])];
                    break;
                }
            }
        }
        nbits -= L;
        if (s == S2_EOF) return (long)o;
        out[o++] = (uint8_t)s;
    }
}

/* ---- table instances + init + id-dispatch API ---- */
static s2_table_t T_EN, T_C2, T_C3;
static const s2_table_t *tab(s2_table_id id) {
    return id == S2_TABLE_EN ? &T_EN : id == S2_TABLE_C2 ? &T_C2 : &T_C3;
}
void codec_init(void) {
    s1_init();
    s2_build_final(&T_EN, S2_LEN_EN);
    s2_build_final(&T_C2, S2_LEN_C2);
    s2_build_final(&T_C3, S2_LEN_C3);
}
long s2_encode_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out)
{ return s2_encode(tab(id), in, n, out); }
long s2_decode_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out)
{ return s2_decode(tab(id), in, n, out); }
long s2_decode_lut_id(s2_table_id id, const uint8_t *in, size_t n, uint8_t *out)
{ return s2_decode_lut(tab(id), in, n, out); }
