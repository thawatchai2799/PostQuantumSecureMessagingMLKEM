/* bench_lib.c - benchmark/verification driver for the library codecs.
 *
 * Times the library-based schemes with exactly the same method as
 * bench_main.c (clock_gettime(CLOCK_MONOTONIC) around one encode call and
 * one decode call, same warm-up, same output format), so that every scheme
 * in the paper is timed from C:
 *
 *   s3     zstd level 3, no dictionary          (libzstd 1.5.7)
 *   s3d    zstd level 3, pre-shared dictionary  (dictionary file argument,
 *          dictionary ID not written into the frame)
 *   hs8    heatshrink LZSS, window 2^8,  lookahead 2^4
 *   hs11   heatshrink LZSS, window 2^11, lookahead 2^4
 *   ux     Unishox2 (simple API, default preset)
 *   s4     UTF-8 passthrough (memcpy), the zero-work reference
 *
 * Contexts, dictionaries and coder state are created once, outside the
 * timed region, and reused for every message, as a long-running node
 * would do. Per message, only the encode or decode call is timed.
 *
 * Usage:
 *   bench_lib enc  <scheme> <in> <out> [dict]
 *   bench_lib dec  <scheme> <in> <out> [dict]
 *   bench_lib time <scheme> <in> <reps> [dict]
 * "time" prints "op,rep,ns" rows plus one "size,-1,<encoded bytes>" row.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define ZSTD_STATIC_LINKING_ONLY
#include "zstd.h"
#include "heatshrink_encoder.h"
#include "heatshrink_decoder.h"
#include "unishox2.h"

#define MAXIN  (1u << 20)
#define MAXOUT (1u << 22)
static uint8_t g_in[MAXIN], g_out[MAXOUT], g_back[MAXOUT];

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void die(const char *msg) { fprintf(stderr, "%s\n", msg); exit(2); }

/* ---------------- zstd (S3, S3d) ---------------- */
static ZSTD_CCtx *g_cctx;
static ZSTD_DCtx *g_dctx;
static ZSTD_CDict *g_cdict;
static ZSTD_DDict *g_ddict;

static void zstd_setup(const char *dictpath) {
    g_cctx = ZSTD_createCCtx();
    g_dctx = ZSTD_createDCtx();
    if (!g_cctx || !g_dctx) die("zstd: context allocation failed");
    if (dictpath) {
        static uint8_t dict[1 << 16];
        FILE *f = fopen(dictpath, "rb");
        if (!f) die("zstd: cannot open dictionary");
        size_t dn = fread(dict, 1, sizeof dict, f);
        fclose(f);
        g_cdict = ZSTD_createCDict(dict, dn, 3);
        g_ddict = ZSTD_createDDict(dict, dn);
        if (!g_cdict || !g_ddict) die("zstd: dictionary load failed");
        ZSTD_CCtx_refCDict(g_cctx, g_cdict);
        ZSTD_DCtx_refDDict(g_dctx, g_ddict);
    } else {
        ZSTD_CCtx_setParameter(g_cctx, ZSTD_c_compressionLevel, 3);
    }
    ZSTD_CCtx_setParameter(g_cctx, ZSTD_c_contentSizeFlag, 1);
    ZSTD_CCtx_setParameter(g_cctx, ZSTD_c_checksumFlag, 0);
    ZSTD_CCtx_setParameter(g_cctx, ZSTD_c_dictIDFlag, 0);
}

static long zstd_enc(const uint8_t *in, size_t n, uint8_t *out) {
    size_t r = ZSTD_compress2(g_cctx, out, MAXOUT, in, n);
    return ZSTD_isError(r) ? -1 : (long)r;
}

static long zstd_dec(const uint8_t *in, size_t n, uint8_t *out) {
    size_t r = ZSTD_decompressDCtx(g_dctx, out, MAXOUT, in, n);
    return ZSTD_isError(r) ? -1 : (long)r;
}

/* ---------------- heatshrink (S5) ---------------- */
static heatshrink_encoder *g_hse;
static heatshrink_decoder *g_hsd;

static void hs_setup(int w, int l) {
    g_hse = heatshrink_encoder_alloc((uint8_t)w, (uint8_t)l);
    g_hsd = heatshrink_decoder_alloc(256, (uint8_t)w, (uint8_t)l);
    if (!g_hse || !g_hsd) die("heatshrink: allocation failed");
}

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

/* ---------------- Unishox2 (S6) ---------------- */
static long ux_enc(const uint8_t *in, size_t n, uint8_t *out) {
    return unishox2_compress_simple((const char *)in, (int)n, (char *)out);
}

static long ux_dec(const uint8_t *in, size_t n, uint8_t *out) {
    return unishox2_decompress_simple((const char *)in, (int)n, (char *)out);
}

/* ---------------- UTF-8 passthrough (S4) ---------------- */
static long s4_enc(const uint8_t *in, size_t n, uint8_t *out) { memcpy(out, in, n); return (long)n; }

/* ---------------- dispatch ---------------- */
typedef long (*codec_fn)(const uint8_t *, size_t, uint8_t *);
static codec_fn ENC, DEC;

static void pick(const char *s, const char *dict) {
    if (!strcmp(s, "s3"))   { zstd_setup(NULL); ENC = zstd_enc; DEC = zstd_dec; return; }
    if (!strcmp(s, "s3d"))  { if (!dict) die("s3d needs a dictionary file");
                              zstd_setup(dict); ENC = zstd_enc; DEC = zstd_dec; return; }
    if (!strcmp(s, "hs8"))  { hs_setup(8, 4);  ENC = hs_enc; DEC = hs_dec; return; }
    if (!strcmp(s, "hs11")) { hs_setup(11, 4); ENC = hs_enc; DEC = hs_dec; return; }
    if (!strcmp(s, "ux"))   { ENC = ux_enc; DEC = ux_dec; return; }
    if (!strcmp(s, "s4"))   { ENC = s4_enc; DEC = s4_enc; return; }
    die("bad scheme (s3|s3d|hs8|hs11|ux|s4)");
}

int main(int argc, char **argv) {
    if (argc < 5) die("usage: bench_lib <enc|dec|time> <scheme> <in> <out|reps> [dict]");
    const char *op = argv[1];
    pick(argv[2], argc >= 6 ? argv[5] : NULL);
    FILE *f = fopen(argv[3], "rb");
    if (!f) die("cannot open input");
    size_t n = fread(g_in, 1, MAXIN, f);
    fclose(f);

    if (!strcmp(op, "enc") || !strcmp(op, "dec")) {
        long m = !strcmp(op, "enc") ? ENC(g_in, n, g_out) : DEC(g_in, n, g_out);
        if (m < 0) die("codec error");
        FILE *fo = fopen(argv[4], "wb");
        if (!fo) die("cannot open output");
        fwrite(g_out, 1, (size_t)m, fo);
        fclose(fo);
        return 0;
    }
    if (!strcmp(op, "time")) {
        int reps = atoi(argv[4]);
        long m = ENC(g_in, n, g_out);
        if (m < 0) die("codec error");
        long back = DEC(g_out, (size_t)m, g_back);
        if (back != (long)n || memcmp(g_back, g_in, n)) die("round trip mismatch");
        for (int r = 0; r < 5; r++) { ENC(g_in, n, g_out); DEC(g_out, (size_t)m, g_back); }
        printf("op,rep,ns\n");
        printf("size,-1,%ld\n", m);
        for (int r = 0; r < reps; r++) {
            uint64_t a = now_ns();
            volatile long e = ENC(g_in, n, g_out);
            uint64_t b = now_ns();
            volatile long d = DEC(g_out, (size_t)m, g_back);
            uint64_t c = now_ns();
            (void)e; (void)d;
            printf("encode,%d,%llu\n", r, (unsigned long long)(b - a));
            printf("decode,%d,%llu\n", r, (unsigned long long)(c - b));
        }
        return 0;
    }
    die("bad op");
    return 2;
}
