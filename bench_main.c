/* bench_main.c — benchmark/verification driver for codec.c.
 * Buffers here are measurement scaffolding, NOT part of the codec
 * footprint; report codec.o sizes for the paper, not this file's. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "codec.h"

#define MAXIN  (1u << 20)
#define MAXOUT (1u << 22)
static uint8_t g_in[MAXIN], g_out[MAXOUT], g_back[MAXOUT];

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static int pick(const char *s, s2_table_id *id) {
    if (!strcmp(s, "s2en")) { *id = S2_TABLE_EN; return 1; }
    if (!strcmp(s, "s2c2")) { *id = S2_TABLE_C2; return 1; }
    if (!strcmp(s, "s2c3")) { *id = S2_TABLE_C3; return 1; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: bench <enc|dec|time> <s1|s2en|s2c2|s2c3> <in> [out|reps]\n"); return 2; }
    codec_init();
    const char *op = argv[1], *sch = argv[2];
    FILE *f = fopen(argv[3], "rb");
    if (!f) { perror("open"); return 2; }
    size_t n = fread(g_in, 1, MAXIN, f);
    fclose(f);

    int is_s1 = !strcmp(sch, "s1");
    s2_table_id id = S2_TABLE_EN;
    if (!is_s1 && !pick(sch, &id)) { fprintf(stderr, "bad scheme\n"); return 2; }

#define ENC() (is_s1 ? s1_encode(g_in, n, g_out) : s2_encode_id(id, g_in, n, g_out))
#define DEC(m) (is_s1 ? s1_decode(g_out, (size_t)(m), g_back) : s2_decode_id(id, g_out, (size_t)(m), g_back))

    if (!strcmp(op, "enc") || !strcmp(op, "dec")) {
        if (argc < 5) { fprintf(stderr, "need outfile\n"); return 2; }
        long m;
        if (!strcmp(op, "enc")) m = ENC();
        else m = is_s1 ? s1_decode(g_in, n, g_out) : s2_decode_id(id, g_in, n, g_out);
        if (m < 0) { fprintf(stderr, "codec error\n"); return 1; }
        FILE *fo = fopen(argv[4], "wb");
        fwrite(g_out, 1, (size_t)m, fo);
        fclose(fo);
        return 0;
    }
    if (!strcmp(op, "time")) {
        int reps = (argc >= 5) ? atoi(argv[4]) : 30;
        long m = ENC();
        if (m < 0) return 1;
        for (int r = 0; r < 5; r++) { ENC(); DEC(m); }
        printf("op,rep,ns\n");
        for (int r = 0; r < reps; r++) {
            uint64_t a = now_ns();
            volatile long e = ENC();
            uint64_t b = now_ns();
            volatile long d = DEC(m);
            uint64_t c = now_ns();
            (void)e; (void)d;
            printf("encode,%d,%llu\n", r, (unsigned long long)(b - a));
            printf("decode,%d,%llu\n", r, (unsigned long long)(c - b));
        }
        return 0;
    }
    fprintf(stderr, "bad op\n");
    return 2;
}
