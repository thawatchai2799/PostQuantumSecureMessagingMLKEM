/* plat_stm32_inline.h - the parts of the STM32 platform that run inside the
 * timed region (see plat.h). Included only with -DPLAT_STM32. */
#ifndef PLAT_STM32_INLINE_H
#define PLAT_STM32_INLINE_H
#include <stdint.h>

#define DWT_CYCCNT (*(volatile uint32_t *)0xE0001004u)
#define STACK_PATTERN 0xC5ACCE55u

/* Lowest address of the stack region (linker symbol, see link.ld). The
 * stack grows down from _estack toward this address. */
extern uint32_t _sstack[];

static inline __attribute__((always_inline)) uint32_t plat_cycles(void) {
    return DWT_CYCCNT;
}

static inline __attribute__((always_inline)) uint32_t plat_sp(void) {
    uint32_t sp;
    __asm volatile("mov %0, sp" : "=r"(sp));
    return sp;
}

/* Paint [_sstack, sp0). Called from the measuring function with its own
 * stack pointer, so everything below sp0 is free: no frame of ours lies in
 * the painted range (no interrupt is enabled in this firmware). */
static inline __attribute__((always_inline)) void plat_stack_paint(uint32_t sp0) {
    volatile uint32_t *p = _sstack;
    while ((uint32_t)p < sp0) *p++ = STACK_PATTERN;
}

/* Scan upward from _sstack for the first word that no longer holds the
 * pattern; the distance from there to sp0 is the high-water mark of the
 * calls made since plat_stack_paint(sp0). */
static inline __attribute__((always_inline)) uint32_t plat_stack_used(uint32_t sp0) {
    volatile uint32_t *p = _sstack;
    while ((uint32_t)p < sp0 && *p == STACK_PATTERN) p++;
    return sp0 - (uint32_t)p;
}

#endif
