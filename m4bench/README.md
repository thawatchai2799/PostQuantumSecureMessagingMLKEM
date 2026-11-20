# m4bench: on-device measurements on a Cortex-M4

Cycle counts and stack use of the encoding schemes and of ML-KEM-512 on an
STM32 Nucleo-64 board (NUCLEO-F446RE or NUCLEO-F411RE; the same binary runs
on both). This complements the host measurements of the main harness: the
same codec sources, the same inputs (slice 0 of the timing slices), and a
device-side check that every encoded size equals the size the host pipeline
produced.

## What is measured

| record | content |
|---|---|
| `cell` | per (corpus, length, scheme) and repetition: encode cycles, encode stack bytes, decode cycles, decode stack bytes, encoded bytes, the host's encoded bytes, and a pass flag (round trip and size both correct) |
| `kem` | per repetition: ML-KEM-512 key generation, encapsulation and decapsulation cycles and stack bytes, and a pass flag (shared secrets agree) |
| `kem_check` | SHA3-256 of a derandomized pk, ct and ss; must equal the digest in `kem_vector.h`, which the host computed from the same implementation and cross-checked against liboqs 0.16.0 |
| `null` | the measurement overhead: an empty call through the same path |
| `info` | device id, clock and flash settings, build flags, RAM accounting |

Schemes: S1 (6-bit static), S2 domain-matched Huffman with the bit-serial and
the table-driven decoder, S4 (memcpy), S5 heatshrink with windows 2^8 and
2^11, S6 Unishox2. zstd is not included: libzstd is not built for this
target in this study. Corpora C1, C2, C3 at 120, 500, 1,000, 2,000 and 5,000
characters (S1 is skipped on C2, as in the paper).

ML-KEM-512 is [mlkem-native](https://github.com/pq-code-package/mlkem-native)
at the commit vendored by liboqs 0.16.0 (`third_party/mlkem-native`), built
with its portable C backend; the host timings used the same library's
x86-64 backend through liboqs. Cycle counts of this C code are higher than
the hand-optimized assembly of pqm4, which the paper cites alongside.

## Measurement conditions

- Core clock: the reset state, 16 MHz HSI, zero flash wait states, no PLL
  (the pqm4 convention: no flash stalls in the cycle counts). The run prints
  `RCC_CFGR` and `FLASH_ACR` so that this can be verified from the log.
- Cycles: DWT `CYCCNT`, read immediately before and after the call.
- Stack: the unused stack is filled with a pattern before each call and
  scanned afterwards (high-water mark of that call, including its own frame).
- Each call is repeated 5 times (`REPS`); cycle counts on this core are
  deterministic, so the repetitions should agree exactly or nearly so.
- Build: `-O2` (the level of the host timing drivers); `make OPT=-Os` builds
  the level used for the code sizes of Table 8.
- No interrupt is enabled during the run.

## Files

| file | role |
|---|---|
| `bench_core.c` | the benchmark (platform-independent) |
| `plat.h`, `plat_stm32.c`, `plat_stm32_inline.h`, `link.ld` | bare-metal STM32F4 platform: startup, USART2, DWT, stack painting |
| `plat_host.c` | the same benchmark on the PC, for checking logic and data before flashing |
| `kem_glue.c/.h` | ML-KEM-512 through mlkem-native; deterministic `randombytes()` |
| `make_m4_data.py` -> `m4_data.h` | embeds the inputs and the host's encoded sizes |
| `make_kem_vector.c` -> `kem_vector.h` | expected digest of the derandomized key exchange (cross-checked against liboqs) |
| `m4_capture.py` | starts a run over the serial port and saves the output |
| `m4_summary.py` | medians, microseconds, stack maxima, host comparison -> `data/summary_m4_*.txt/.csv` |
| `Makefile` | `make` (firmware), `make host`, `make data`, `make vector` |

## Running

1. Build (needs `arm-none-eabi-gcc`): `make -C m4bench` gives
   `m4bench/build/m4bench.bin`. Prebuilt binaries are in `m4bench/prebuilt/`.
2. Connect the Nucleo board by USB. It appears as a USB drive
   (`NODE_F446RE` or `NODE_F411RE`) and as a serial port (ST-LINK Virtual COM
   Port). Copy `m4bench.bin` onto the drive; the ST-LINK flashes it.
3. `python m4_capture.py <port>` (needs `pip install pyserial`); the script
   sends the start command and saves `m4_raw_<date>.txt`. A run takes a few
   minutes; the Unishox2 encoder at 5,000 characters dominates.
4. `python3 m4bench/m4_summary.py m4_raw_<date>.txt --device STM32F446RE`
   from the repository root writes `data/results_m4_*.csv`,
   `data/summary_m4_*.csv` and `data/summary_m4_*.txt`. The firmware prints
   its device id before the host opens the port, so `--device` names the
   board in the output files; without it the platform name is used.

The captures behind the paper are `data/m4_raw_20261120_154548.txt` (-O2)
and `data/m4_raw_20261120_155521.txt` (-Os), both from a NUCLEO-F446RE, with
their summaries in `data/summary_m4_STM32F446RE_*`.

Before flashing, `make -C m4bench host && m4bench/build/m4bench_host` runs the
identical benchmark on the PC; its last line must be `end,<n>,0`.

## Checks built into the run

A run is valid only if its `end` line reports zero failures: every decode
reproduced its input (S1: the uppercase-folded, alphabet-filtered input),
every encoded size equals the host's size for the same input, and the
derandomized ML-KEM digest equals the host's. The firmware was also run on
QEMU's STM32F405 machine (same peripherals, no cycle counter) before being
flashed.
