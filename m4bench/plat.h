/* plat.h - the small platform interface that bench_core.c needs.
 *
 * Two implementations exist:
 *   plat_stm32.c (+ plat_stm32_inline.h)  bare-metal STM32F4 (NUCLEO-F446RE,
 *                 NUCLEO-F411RE): DWT cycle counter, USART2 output, stack
 *                 painting. Selected with -DPLAT_STM32.
 *   plat_host.c   a POSIX build of the same benchmark, used to verify the
 *                 benchmark logic and the expected sizes before the firmware
 *                 is flashed (its "cycles" are nanoseconds, stack figures 0).
 */
#ifndef PLAT_H
#define PLAT_H
#include <stddef.h>
#include <stdint.h>

int      bench_main(void);               /* the benchmark; returns when the host says quit */

void     plat_init(void);
void     plat_puts(const char *s);       /* blocking text output */
void     plat_putu(uint32_t v);          /* unsigned decimal */
void     plat_puthex(const uint8_t *p, size_t n);
int      plat_wait_start(void);          /* block until the host asks for a run; 0 = run, 1 = quit */
const char *plat_name(void);             /* "stm32f4" or "host" */
uint32_t plat_cpu_hz(void);              /* core clock during the run, 0 if unknown */
uint32_t plat_static_bytes(void);        /* .data + .bss of the image (0 on the host) */
uint32_t plat_heap_used(void);           /* bytes handed out by malloc() so far (0 on the host) */

/* The three functions below sit inside the timed region, so on the device
 * they are inline: a call would push its own frame onto the stack being
 * measured and add its own cycles.
 *   plat_cycles()         free-running cycle counter
 *   plat_sp()             the caller's stack pointer
 *   plat_stack_paint(sp)  fill the unused stack below sp with a pattern
 *   plat_stack_used(sp)   bytes below sp written since the paint           */
#ifdef PLAT_STM32
#include "plat_stm32_inline.h"
#else
uint32_t plat_cycles(void);
uint32_t plat_sp(void);
void     plat_stack_paint(uint32_t sp0);
uint32_t plat_stack_used(uint32_t sp0);
#endif

#endif
