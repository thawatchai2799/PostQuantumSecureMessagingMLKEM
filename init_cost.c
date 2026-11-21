/* init_cost.c - one-time initialization cost of each codec on the host.
 *
 * The per-message figures of the timing drivers exclude whatever a codec does
 * once at start-up. This program times those one-time steps, each as the
 * median of REPS repetitions of a fresh set-up (clock_gettime, CLOCK_MONOTONIC):
 *
 *   S1/S2   codec_init(): the 6-bit tables and, for the three Huffman domains,
 *           the canonical code from its 257 code lengths plus the 9-bit
 *           table-driven decoder's lookup table (codec.c)
 *   S2      one domain only (s2_build_final through codec_init is three)
 *   S3      ZSTD_createCCtx + ZSTD_createDCtx (and the first compression,
 *           which is when libzstd sizes and allocates its workspace)
 *   S3d     as S3 plus ZSTD_createCDict / ZSTD_createDDict for the 2,048-byte
 *           pre-shared dictionary
 *   S5      heatshrink_encoder_alloc + heatshrink_decoder_alloc (2^8 and 2^11)
 *   S6      none (Unishox2 keeps its model in constant data)
 *
 * Build (same flags and third-party objects as bench_lib):
 *   gcc -O2 -Wall -o init_cost init_cost.c codec.c build/heatshrink_encoder.o
 *       build/heatshrink_decoder.o <zstd>/lib/libzstd.a -I<zstd>/lib -Ithird_party/heatshrink
 * Usage:  ./init_cost <dictionary file> <sample input file>
 * Output: CSV lines  step,median_us,min_us,max_us
 */
#define _POSIX_C_SOURCE 199309L   /* clock_gettime */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "codec.h"
#include "heatshrink_decoder.h"
#include "heatshrink_encoder.h"
#include "zstd.h"

#define REPS 201

static double now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec / 1e3;
}

static int cmp(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return (x > y) - (x < y);
}

static void report(const char *step, double *t) {
    qsort(t, REPS, sizeof *t, cmp);
    printf("%s,%.2f,%.2f,%.2f\n", step, t[REPS / 2], t[0], t[REPS - 1]);
}

static unsigned char *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *p = malloc((size_t)len + 1);
    if (!p || fread(p, 1, (size_t)len, f) != (size_t)len) { perror(path); exit(1); }
    fclose(f);
    *n = (size_t)len;
    return p;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <dictionary> <input>\n", argv[0]); return 2; }
    size_t dn, in_n;
    unsigned char *dict = slurp(argv[1], &dn);
    unsigned char *in = slurp(argv[2], &in_n);
    size_t cap = ZSTD_compressBound(in_n);
    unsigned char *out = malloc(cap);
    double t[REPS];
    volatile long sink = 0;

    printf("step,median_us,min_us,max_us\n");
    printf("# input %s (%zu bytes), dictionary %zu bytes, %d repetitions\n", argv[2], in_n, dn, REPS);

    /* S1 + S2 (three domains, both decoders): codec_init() rebuilds every table */
    for (int r = 0; r < REPS; r++) {
        double a = now_us(); codec_init(); t[r] = now_us() - a;
    }
    report("S1+S2 codec_init (3 Huffman domains, canonical code + 9-bit LUT)", t);

    /* heatshrink: allocate encoder and decoder state */
    for (int w = 8; w <= 11; w += 3) {
        for (int r = 0; r < REPS; r++) {
            double a = now_us();
            heatshrink_encoder *e = heatshrink_encoder_alloc((uint8_t)w, 4);
            heatshrink_decoder *d = heatshrink_decoder_alloc(256, (uint8_t)w, 4);
            t[r] = now_us() - a;
            heatshrink_encoder_free(e); heatshrink_decoder_free(d);
        }
        report(w == 8 ? "S5 heatshrink alloc encoder+decoder (2^8)" : "S5 heatshrink alloc encoder+decoder (2^11)", t);
    }

    /* zstd: contexts, and the first compression that allocates the workspace */
    for (int r = 0; r < REPS; r++) {
        double a = now_us();
        ZSTD_CCtx *c = ZSTD_createCCtx(); ZSTD_DCtx *d = ZSTD_createDCtx();
        t[r] = now_us() - a;
        ZSTD_freeCCtx(c); ZSTD_freeDCtx(d);
    }
    report("S3 ZSTD_createCCtx + ZSTD_createDCtx", t);
    for (int r = 0; r < REPS; r++) {
        ZSTD_CCtx *c = ZSTD_createCCtx();
        ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, 3);
        double a = now_us();
        sink += (long)ZSTD_compress2(c, out, cap, in, in_n);      /* first call: workspace allocation + compression */
        t[r] = now_us() - a;
        ZSTD_freeCCtx(c);
    }
    report("S3 first compression on a fresh context (allocates the workspace)", t);

    /* zstd with the pre-shared dictionary */
    for (int r = 0; r < REPS; r++) {
        double a = now_us();
        ZSTD_CDict *cd = ZSTD_createCDict(dict, dn, 3);
        t[r] = now_us() - a;
        ZSTD_freeCDict(cd);
    }
    report("S3d ZSTD_createCDict (2,048 B dictionary, level 3)", t);
    for (int r = 0; r < REPS; r++) {
        double a = now_us();
        ZSTD_DDict *dd = ZSTD_createDDict(dict, dn);
        t[r] = now_us() - a;
        ZSTD_freeDDict(dd);
    }
    report("S3d ZSTD_createDDict (2,048 B dictionary)", t);
    {
        ZSTD_CDict *cd = ZSTD_createCDict(dict, dn, 3);
        for (int r = 0; r < REPS; r++) {
            ZSTD_CCtx *c = ZSTD_createCCtx();
            ZSTD_CCtx_refCDict(c, cd);
            double a = now_us();
            sink += (long)ZSTD_compress2(c, out, cap, in, in_n);
            t[r] = now_us() - a;
            ZSTD_freeCCtx(c);
        }
        report("S3d first compression on a fresh context with the dictionary attached", t);
        ZSTD_freeCDict(cd);
    }
    free(dict); free(in); free(out);
    return sink == 0x7fffffff;   /* keep the compressions from being optimized away */
}
