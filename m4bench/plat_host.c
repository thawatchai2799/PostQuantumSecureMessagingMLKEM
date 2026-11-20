/* plat_host.c - POSIX implementation of plat.h, for verifying the benchmark
 * logic on the host. "cycles" are nanoseconds here and stack figures are 0;
 * the correctness checks (round trips, expected sizes, KEM digest) are the
 * same as on the device, so a host run with end,<ok>,0 means the embedded
 * data, the codecs and the output format are right before flashing. */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "plat.h"

static int g_runs;

void plat_init(void) {}

uint32_t plat_cycles(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)((uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec);
}

void plat_puts(const char *s) { fputs(s, stdout); }
void plat_putu(uint32_t v) { printf("%lu", (unsigned long)v); }
void plat_puthex(const uint8_t *p, size_t n) { for (size_t i = 0; i < n; i++) printf("%02x", p[i]); }

int plat_wait_start(void) { return g_runs++ ? 1 : 0; }   /* run once, then quit */
const char *plat_name(void) { return "host"; }
uint32_t plat_cpu_hz(void) { return 0; }
uint32_t plat_static_bytes(void) { return 0; }
uint32_t plat_heap_used(void) { return 0; }

uint32_t plat_sp(void) { return 0; }
void plat_stack_paint(uint32_t sp0) { (void)sp0; }
uint32_t plat_stack_used(uint32_t sp0) { (void)sp0; return 0; }

int main(void) { return bench_main(); }
