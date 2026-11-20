/* link.ld - STM32F446RE / STM32F411RE: 512 KB flash, 128 KB SRAM.
 * The top 32 KB of SRAM is the stack region [_sstack, _estack); the
 * benchmark paints it to measure stack use, so nothing else may live there. */
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 512K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

STACK_SIZE = 32K;
_estack = ORIGIN(RAM) + LENGTH(RAM);
_sstack = _estack - STACK_SIZE;

SECTIONS
{
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text : {
        . = ALIGN(4);
        *(.text*)
        *(.rodata*)
        *(.glue_7*) *(.glue_7t*)
        *(.ARM.extab*)
        . = ALIGN(4);
    } > FLASH
    .ARM.exidx : { *(.ARM.exidx*) } > FLASH
    /DISCARD/ : { *(.init_array*) *(.fini_array*) *(.eh_frame*) }
    _sidata = LOADADDR(.data);
    .data : {
        . = ALIGN(4);
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT > FLASH
    .bss (NOLOAD) : {
        . = ALIGN(4);
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } > RAM AT > RAM
    ASSERT(_ebss <= _sstack, "static data overlaps the stack region")
}
