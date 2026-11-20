/* zstd_mem.c - working memory of libzstd for the S3 and S3d configurations.
 *
 * The codec drivers report static section sizes for the table codecs, which
 * allocate nothing at run time. libzstd allocates its working memory at run
 * time, sized by the compression level, the window, and the declared input
 * size, so its footprint is measured here with the library's own accounting:
 * ZSTD_sizeof_CCtx()/ZSTD_sizeof_DCtx() after a real (de)compression of an
 * input of the given size, and ZSTD_sizeof_CDict()/ZSTD_sizeof_DDict() for the
 * pre-shared dictionary. The contexts are set up exactly as in bench_lib.c
 * (level 3, content size written, no checksum, no dictionary id).
 *
 * Build:  cc -O2 -I<zstd>/lib zstd_mem.c <zstd>/lib/libzstd.a -o zstd_mem
 * Usage:  ./zstd_mem <dictionary file> <input file> [<input file> ...]
 * Output: one line per input file and configuration (CSV), then the
 *         context sizes libzstd estimates when the input size is unknown.
 */
#define ZSTD_STATIC_LINKING_ONLY
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "zstd.h"

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

static void setup(ZSTD_CCtx *c, const ZSTD_CDict *cd) {
    if (cd) ZSTD_CCtx_refCDict(c, cd);
    else ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, 3);
    ZSTD_CCtx_setParameter(c, ZSTD_c_contentSizeFlag, 1);
    ZSTD_CCtx_setParameter(c, ZSTD_c_checksumFlag, 0);
    ZSTD_CCtx_setParameter(c, ZSTD_c_dictIDFlag, 0);
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <dictionary> <input> [...]\n", argv[0]); return 2; }
    size_t dn;
    unsigned char *dict = slurp(argv[1], &dn);
    ZSTD_CDict *cdict = ZSTD_createCDict(dict, dn, 3);
    ZSTD_DDict *ddict = ZSTD_createDDict(dict, dn);
    if (!cdict || !ddict) { fprintf(stderr, "dictionary load failed\n"); return 1; }
    printf("libzstd,%s\n", ZSTD_versionString());
    printf("dictionary_bytes,%zu\n", dn);
    printf("cdict_bytes,%zu\n", ZSTD_sizeof_CDict(cdict));
    printf("ddict_bytes,%zu\n", ZSTD_sizeof_DDict(ddict));
    printf("input_bytes,config,cctx_bytes,dctx_bytes,compressed_bytes\n");
    for (int i = 2; i < argc; i++) {
        size_t n;
        unsigned char *in = slurp(argv[i], &n);
        size_t cap = ZSTD_compressBound(n);
        unsigned char *out = malloc(cap), *back = malloc(n + 1);
        for (int withdict = 0; withdict < 2; withdict++) {
            ZSTD_CCtx *c = ZSTD_createCCtx();
            ZSTD_DCtx *d = ZSTD_createDCtx();
            setup(c, withdict ? cdict : NULL);
            if (withdict) ZSTD_DCtx_refDDict(d, ddict);
            size_t cn = ZSTD_compress2(c, out, cap, in, n);
            if (ZSTD_isError(cn)) { fprintf(stderr, "compress: %s\n", ZSTD_getErrorName(cn)); return 1; }
            size_t bn = ZSTD_decompressDCtx(d, back, n + 1, out, cn);
            if (ZSTD_isError(bn) || bn != n || memcmp(back, in, n)) { fprintf(stderr, "round trip failed\n"); return 1; }
            printf("%zu,%s,%zu,%zu,%zu\n", n, withdict ? "S3d" : "S3", ZSTD_sizeof_CCtx(c), ZSTD_sizeof_DCtx(d), cn);
            ZSTD_freeCCtx(c);
            ZSTD_freeDCtx(d);
        }
        free(in); free(out); free(back);
    }
    /* a streaming compressor that does not know the input size sizes its window for the level alone */
    ZSTD_compressionParameters cp = ZSTD_getCParams(3, ZSTD_CONTENTSIZE_UNKNOWN, 0);
    printf("unknown_size_level3_window_log,%u\n", cp.windowLog);
    printf("unknown_size_level3_cctx_estimate_bytes,%zu\n", ZSTD_estimateCCtxSize_usingCParams(cp));
    ZSTD_freeCDict(cdict);
    ZSTD_freeDDict(ddict);
    free(dict);
    return 0;
}
