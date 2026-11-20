/* plat_stm32.c - bare-metal platform for NUCLEO-F446RE and NUCLEO-F411RE.
 *
 * Clock: the reset state is kept on purpose: the core runs from the 16 MHz
 * internal RC oscillator (HSI) with zero flash wait states and the ART
 * accelerator at its reset setting, so that cycle counts are not inflated by
 * flash stalls and are comparable with the convention of pqm4, which clocks
 * its boards so that flash needs no wait states. No PLL is configured.
 *
 * Output: USART2 on PA2 (TX) and PA3 (RX), 115200 8N1. On a Nucleo-64 board
 * these pins are wired to the ST-LINK's virtual COM port, so the text
 * arrives on the PC through the same USB cable that powers the board.
 *
 * Cycle counter: the Cortex-M4 DWT CYCCNT, 32-bit, one count per core clock.
 *
 * Startup: vector table, .data copy, .bss clear, then bench_main(). No
 * interrupt is enabled; SysTick is off. A hard fault prints "fault" and
 * halts. malloc() is a bump allocator over a static arena (heatshrink
 * allocates its coder state once at start-up and never frees it); free() is
 * a no-op.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "plat.h"

/* ---- registers (identical on STM32F446 and STM32F411) ---- */
#define REG(a) (*(volatile uint32_t *)(a))
#define RCC_CFGR      REG(0x40023808u)
#define RCC_AHB1ENR   REG(0x40023830u)
#define RCC_APB1ENR   REG(0x40023840u)
#define FLASH_ACR     REG(0x40023C00u)
#define GPIOA_MODER   REG(0x40020000u)
#define GPIOA_OSPEEDR REG(0x40020008u)
#define GPIOA_PUPDR   REG(0x4002000Cu)
#define GPIOA_AFRL    REG(0x40020020u)
#define USART2_SR     REG(0x40004400u)
#define USART2_DR     REG(0x40004404u)
#define USART2_BRR    REG(0x40004408u)
#define USART2_CR1    REG(0x4000440Cu)
#define DEMCR         REG(0xE000EDFCu)
#define DWT_CTRL      REG(0xE0001000u)
#define DWT_CYCCNT_R  REG(0xE0001004u)
#define DBGMCU_IDCODE REG(0xE0042000u)

#define HSI_HZ 16000000u
#define BAUD   115200u

/* ---- UART ---- */
static void uart_init(void) {
    RCC_AHB1ENR |= 1u << 0;                       /* GPIOA clock */
    RCC_APB1ENR |= 1u << 17;                      /* USART2 clock */
    (void)RCC_APB1ENR;                            /* let the enable settle */
    GPIOA_MODER = (GPIOA_MODER & ~((3u << 4) | (3u << 6))) | (2u << 4) | (2u << 6);   /* PA2, PA3: AF */
    GPIOA_OSPEEDR |= (2u << 4) | (2u << 6);                                            /* fast */
    GPIOA_PUPDR = (GPIOA_PUPDR & ~((3u << 4) | (3u << 6))) | (1u << 6);                /* PA3 pull-up */
    GPIOA_AFRL = (GPIOA_AFRL & ~((0xFu << 8) | (0xFu << 12))) | (7u << 8) | (7u << 12); /* AF7 = USART2 */
    /* APB1 clock = HSI = 16 MHz at reset; oversampling by 16:
     * USARTDIV = 16e6 / (16 * 115200) = 8.6806 -> mantissa 8, fraction 11/16 */
    USART2_BRR = (8u << 4) | 11u;
    USART2_CR1 = (1u << 13) | (1u << 3) | (1u << 2);   /* UE, TE, RE */
}

static void uart_putc(char c) {
    while (!(USART2_SR & (1u << 7))) {}           /* TXE */
    USART2_DR = (uint8_t)c;
}

static int uart_getc(void) {
    while (!(USART2_SR & (1u << 5))) {}           /* RXNE */
    return (int)(USART2_DR & 0xFFu);
}

static void uart_flush(void) {
    while (!(USART2_SR & (1u << 6))) {}           /* TC */
}

void plat_puts(const char *s) {
    while (*s) uart_putc(*s++);
}

void plat_putu(uint32_t v) {
    char buf[11];
    int i = 10;
    buf[i] = 0;
    do { buf[--i] = (char)('0' + v % 10u); v /= 10u; } while (v);
    plat_puts(buf + i);
}

void plat_puthex(const uint8_t *p, size_t n) {
    static const char H[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) { uart_putc(H[p[i] >> 4]); uart_putc(H[p[i] & 15]); }
}

/* ---- cycle counter ---- */
static int dwt_init(void) {
    DEMCR |= 1u << 24;                            /* TRCENA */
    DWT_CYCCNT_R = 0;
    DWT_CTRL |= 1u;                               /* CYCCNTENA */
    uint32_t a = DWT_CYCCNT_R;
    for (volatile int i = 0; i < 100; i++) {}
    return DWT_CYCCNT_R != a;                     /* 1 if the counter runs */
}

/* ---- platform API ---- */
static int g_dwt_ok;

void plat_init(void) {
    uart_init();
    g_dwt_ok = dwt_init();
}

const char *plat_name(void) { return "stm32f4"; }

uint32_t plat_cpu_hz(void) {
    /* SWS = 00 means HSI is the system clock; HPRE = 0 means AHB prescaler 1 */
    if (((RCC_CFGR >> 2) & 3u) == 0 && ((RCC_CFGR >> 4) & 0xFu) < 8u) return HSI_HZ;
    return 0;
}

int plat_wait_start(void) {
    plat_puts("ready,send r to run\n");
    plat_puts("info,dev_id,"); plat_putu(DBGMCU_IDCODE & 0xFFFu); plat_puts("\n");   /* 1057 = F446, 1073 = F411 */
    uart_flush();
    for (;;) {
        int c = uart_getc();
        if (c == 'r' || c == 'R') break;
    }
    plat_puts("info,rcc_cfgr,"); plat_putu(RCC_CFGR); plat_puts("\n");
    plat_puts("info,flash_acr,"); plat_putu(FLASH_ACR); plat_puts("\n");
    plat_puts("info,dwt,"); plat_putu((uint32_t)g_dwt_ok); plat_puts("\n");
    return 0;
}

/* ---- bump allocator (heatshrink coder state, allocated once) ---- */
static uint8_t g_arena[24 * 1024] __attribute__((aligned(8)));
static size_t g_arena_used;

uint32_t plat_heap_used(void) { return (uint32_t)g_arena_used; }

void *malloc(size_t n) {
    n = (n + 7u) & ~(size_t)7u;
    if (g_arena_used + n > sizeof g_arena) return NULL;
    void *p = g_arena + g_arena_used;
    g_arena_used += n;
    return p;
}
void free(void *p) { (void)p; }

/* ---- startup ---- */
extern uint32_t _sidata[], _sdata[], _edata[], _sbss[], _ebss[], _estack[];
void Reset_Handler(void);

uint32_t plat_static_bytes(void) {
    return (uint32_t)((uintptr_t)_edata - (uintptr_t)_sdata) + (uint32_t)((uintptr_t)_ebss - (uintptr_t)_sbss);
}

void Reset_Handler(void) {
    uint32_t *src = _sidata, *dst = _sdata;
    while (dst < _edata) *dst++ = *src++;
    for (dst = _sbss; dst < _ebss; dst++) *dst = 0;
    (void)bench_main();
    for (;;) {}
}

static void Fault_Handler(void) {
    plat_puts("\nfault\n");
    for (;;) {}
}

static void Default_Handler(void) {
    for (;;) {}
}

__attribute__((section(".isr_vector"), used))
const void *const g_vectors[16] = {
    _estack, Reset_Handler, Default_Handler, Fault_Handler,   /* SP, Reset, NMI, HardFault */
    Fault_Handler, Fault_Handler, Fault_Handler, 0,           /* MemManage, BusFault, UsageFault, - */
    0, 0, 0, Default_Handler,                                 /* -, -, -, SVCall */
    Default_Handler, 0, Default_Handler, Default_Handler,     /* DebugMon, -, PendSV, SysTick */
};
