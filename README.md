# mlkem-encoding-benchmark

Benchmark suite for **pre-encryption plaintext encoding strategies in
ML-KEM-based secure messaging** (KEM-DEM construction per FIPS 203).

This repository accompanies a research paper comparing four encoding
schemes — a 6-bit static table code, pre-shared canonical Huffman
coding, zstd compression, and a UTF-8 passthrough baseline — measured
inside a standard `ML-KEM-512 -> HKDF-SHA256 -> AES-256-GCM` pipeline.
It contains everything needed to reproduce the paper's numbers: corpus
generators (seeded), reference and C implementations of the codecs,
the measurement harness, statistical summaries, figure generation, and
the raw measurement data reported in the paper.

## Repository layout

```
s3_dict.py               S3d: zstd with a PRE-SHARED pre-trained dictionary
                         (fair counterpart of S2's pre-shared table)
make_s2_train_en.py      Builds s2_train.txt (English pre-shared training
                         text) from Gutenberg #161 — disjoint from C1 (#1342)
eval_dict_size.py        S3d sensitivity to dictionary size (payload only)
make_tables.py           Prints the paper's tables (Markdown) from a results dir
codec.c / codec.h        C implementations of S1 (6-bit) and S2 (Huffman);
                         static allocation only, embedded-friendly. S2 has
                         two decoders: bit-serial (reference) and
                         table-driven (9-bit lookup, production style)
s5_heatshrink.py         S5: heatshrink LZSS (MCU-class compressor, stateless),
                         two window sizes (256 B, 2 KiB)
s6_unishox.py            S6: Unishox2 short-string coder (stateless), via ctypes
bench_lib.c              C timing driver for the library codecs (zstd, zstd +
                         dictionary, heatshrink, Unishox2, UTF-8); same clock
                         and output format as bench_main.c
verify_lib.py            Byte-exact check: bench_lib output == Python encoders
make_slices.py           5 disjoint input slices per corpus and length (timing)
make_c5_intel.py         Corpus C5: real sensor readings (Intel Berkeley Lab
                         data set, data.txt.gz must be downloaded)
third_party/             Vendored heatshrink 0.4.1 and Unishox2 1.0.4 (unmodified;
                         see third_party/README.md); zstd 1.5.7 is downloaded and
                         checked against a pinned SHA-256 by run_all.py
bench_main.c             CLI driver for the C codecs (encode/decode/time)
s2_tables.h              Auto-generated canonical Huffman tables (do not edit)
gen_s2_tables.py         Regenerates s2_tables.h from the training corpora
alphabet54.py            Single source of truth for the 54-character table
s1_sixbit.py             Python reference implementation of S1
s2_huffman.py            Python reference implementation of S2
make_c1_english.py       Corpus C1: English text (Project Gutenberg #1342)
make_c2_iot.py           Corpus C2: IoT JSON telemetry (seeded generator)
make_c3_uniform.py       Corpus C3: uniform over the 54-char alphabet (seeded)
s2_train*.txt            Pre-shared Huffman training corpora (seed-disjoint
                         from all test slices)
harness.py               KEM-DEM pipeline benchmark (liboqs + AES-256-GCM)
test_roundtrip.py        Python round-trip verification suite
verify_c.py              Byte-exactness check: C output == Python reference
run_phase3.sh            Orchestrator: build -> corpus -> verify -> measure
                         -> summarize -> integrity check
analyze.py               Summarizes harness CSVs (mean/SD)
make_figures.py          Regenerates the paper's figures from data/
data/                    Raw measurement CSVs reported in the paper (n=100)
figures/                 Generated figures
```

## Requirements

- Linux (tested on Ubuntu 24.04, x86-64; WSL2 works)
- gcc, cmake, make, libssl-dev
- Python 3.10+ with packages in `requirements.txt`
- Optional: `gcc-arm-none-eabi` (ARM footprint), `valgrind` (instruction
  counts)

### Install liboqs (minimal ML-KEM-512 build)

```bash
git clone --depth 1 https://github.com/open-quantum-safe/liboqs
cmake -S liboqs -B liboqs/build -DCMAKE_ASM_COMPILER=gcc \
      -DOQS_MINIMAL_BUILD="KEM_ml_kem_512" -DBUILD_SHARED_LIBS=ON \
      -DOQS_BUILD_ONLY_LIB=ON -DCMAKE_INSTALL_PREFIX=$HOME/_oqs
make -C liboqs/build -j$(nproc) install
```

### Python environment

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 -c "import oqs; print(oqs.oqs_version())"   # sanity check
```

## Quick start — full reproduction

```bash
./run_phase3.sh 100
```

This single command:

1. builds the C codecs and regenerates the Huffman tables,
2. generates all corpora (C1 downloads Gutenberg #1342; C2/C3 are
   seeded — all provenance is written next to the files),
3. runs the Python round-trip suite and the C-vs-Python byte-exactness
   suite, **aborting on any failure**,
4. measures KEM session costs and the full encode->encrypt->decrypt->
   decode pipeline (n=100 repetitions per point),
5. measures C codec latency for every scheme x corpus x length,
6. collects code-size footprint (x86 `-Os`; ARM Cortex-M4 if the
   cross-toolchain is present),
7. writes summaries, and
8. verifies output integrity: every CSV must contain exactly the
   requested number of repetitions (guards against stale-artifact
   contamination between runs).

All outputs land in a timestamped directory under `results_phase3/`.

## Individual tools

```bash
# Byte-exact check of the C codecs against the Python reference
gcc -O2 -o bench codec.c bench_main.c
python3 verify_c.py

# Time a single codec on a single file (CSV to stdout)
./bench time s2en corpus/c1/c1_20000.txt 100        # bit-serial S2 decoder
./bench time s2en-lut corpus/c1/c1_20000.txt 100    # table-driven S2 decoder

# Instruction counts (deterministic; needs valgrind) - run_all.py does this
valgrind --tool=callgrind --callgrind-out-file=/dev/null ./bench dec s2en-lut enc.bin out.txt

# Regenerate the paper's figures from the committed data
python3 make_figures.py --datadir data --outdir figures

# Codec-only footprint (no benchmark buffers)
gcc -Os -c codec.c -o codec_os.o && size codec_os.o
arm-none-eabi-gcc -Os -mcpu=cortex-m4 -c codec.c -o codec_arm.o \
  && arm-none-eabi-size codec_arm.o
```

## Timing: every scheme in C, five input slices per cell

`run_all.py` times every scheme from C with `clock_gettime(CLOCK_MONOTONIC)`:
S1 and S2 through `bench`, the library codecs through `bench_lib`. Contexts,
dictionaries and coder state are created once outside the timed region. Each
(corpus, length) cell is timed on 5 disjoint input slices (`make_slices.py`),
100 repetitions each; a cell's value is the median over slices of the
per-slice medians, with the min-max over slices as its range.
`results_c_sizes.csv` records the encoded size of every slice, so payload
crossovers can be checked on all five inputs. The Python pipeline is still
run for the end-to-end KEM-DEM verification and the payload tables.

## C5: real sensor readings

C5 uses the Intel Berkeley Research Lab data set (54 motes, 2004). Download
`data.txt.gz` (34 MB) from https://db.csail.mit.edu/labdata/data.txt.gz and
place it next to `run_all.py`; `make_c5_intel.py` keeps each reading as the
text line the data set distributes, takes the test stream from the start of
the file and the training text from reading 1,000,000 on.

## S2 decoders

`codec.c` contains two canonical-Huffman decoders that produce identical
output (checked byte-for-byte by `verify_c.py`): `s2_decode` walks the
stream one bit at a time (the reference), and `s2_decode_lut` peeks 9 bits,
resolves (length, symbol) from a 512-entry lookup table (1 KiB per code
table, built at start-up from the code lengths) and falls back to the
canonical walk only for codes longer than 9 bits. `run_all.py` times both
(`results_c_latency.csv`, schemes `s2xx` and `s2xx-lut`) and records
instruction counts for both in `callgrind.txt` when valgrind is installed.

## S5: heatshrink (MCU-class compressor)

libzstd needs far more working memory than a constrained node has, so S5
adds heatshrink, an LZSS compressor written for microcontrollers (fixed
buffers, no heap), through the `heatshrink2` binding of the reference C
code. Two configurations are measured, both stateless: `S5-hs8` (256 B
window, the usual MCU setting) and `S5-hs11` (2 KiB window, the library
default, comparable in working memory to the S3d dictionary).

## S3d: pre-shared zstd dictionaries (fairness extension)

S2 is granted a pre-shared Huffman table; to keep the comparison
symmetric, S3d grants zstd the equivalent pre-shared state: a dictionary
trained (zstd COVER trainer, level 3) on the *same* seed-disjoint
training corpora, capped at 2,048 B (S2's expanded decode table is ~2 KB).
The dictionary ID is not written into frames (`write_dict_id=False`), so
nothing is transmitted per message. Two configurations mirror S2:
`S3d-dom` (domain-matched dictionary) and `S3d-en` (English dictionary
applied to every corpus). Dictionary bytes, IDs and SHA-256 are written
to `s3_dicts/` and copied into every results directory.
`eval_dict_size.py` sweeps the cap over 256–4,096 B.

Message lengths now include 120 (≈ one C2 telemetry record), 2,000 and
3,000 characters, so the S2/zstd crossover is located rather than
bracketed. All earlier slices are byte-identical prefixes of the same
streams (same seeds/offsets), so previously reported numbers reproduce
unchanged.

The English pre-shared training text is built by `make_s2_train_en.py`
from Gutenberg #161 (*Sense and Sensibility*): same author and register
as C1 but a different book, so no passage of the test corpus can leak
into the pre-shared state. (Earlier releases shipped a 452-character
paragraph repeated 60 times as `s2_train.txt`; adequate only as a crude
byte-frequency proxy.)

## Notes on methodology

- **S1 out-of-alphabet policy:** S1 is evaluated only on corpora fully
  covered (after uppercase folding) by its 54-character table; on the
  JSON corpus it is reported as N/A rather than silently dropping data.
- **Fairness:** every scheme's shared state (the 6-bit table, the
  Huffman code lengths) is pre-shared; nothing is transmitted per
  message. Huffman tables are trained on corpora seed-disjoint from all
  test slices; both an English-trained table (S2-en) and domain-matched
  tables (S2-dom) are evaluated.
- **Timing:** C implementations for all timed codecs; median reported
  alongside mean +/- SD; n=100 repetitions after warm-up.
- **Determinism:** encoded sizes (bits/char) are deterministic and
  independent of repetition count; `data/` contains the exact CSVs
  used in the paper.


## Reproducibility notes

- **C1 edition sensitivity.** Project Gutenberg occasionally re-typesets its
  files, so a future download of eText #1342 may differ from the copy used
  for the published measurements (an older ASCII mirror, for example, lacks
  the typographic quotes present in the current UTF-8 edition). The exact
  payload numbers reported in the paper are preserved in `data/`; treat
  those CSVs as ground truth when comparing reproduction runs.
- **AEAD timestamps in `results_pipeline.csv`.** The `t_aead_*` columns can
  be inflated by allocator/cache state left behind by the pure-Python
  encoders that run immediately before them; isolated AES-256-GCM on this
  class of hardware processes ~20 kB in a few microseconds. Treat these
  columns as upper bounds; the paper does not rely on them.
- **Codec fold semantics.** The C codec case-folds ASCII a-z only. The
  Python reference uses `str.upper()`, which for a few exotic Unicode
  characters (e.g. ß, ligatures) maps into the 54-character table where C
  drops bytes. All shipped corpora contain none of these characters, and
  byte-exact C-vs-Python equivalence is verified on every corpus file by
  `verify_c.py`.

## License

MIT — see `LICENSE`.

## Extension: KEM parameter-set sweep and C4 robustness corpus

Added after the initial release (all verified cross-machine):

- `kem_sweep.py` — benchmarks ML-KEM-512/768/1024 session costs with the
  same methodology as `harness.py` (n=100, warm-up 5, per-op
  `perf_counter_ns`). Fails fast with rebuild instructions if the local
  liboqs build lacks a parameter set. Output: `data/results_kem_sweep.csv`.
- `make_c4_modern.py` — seeded generator (seed 20260830) for corpus C4:
  modern chat-style English messages. Writes `s2_train_c4.txt` (30,000-char
  pre-shared training split) and `corpus_c4/c4_{500,1000,5000,20000}.txt`
  (disjoint held-out test split). Kept outside `corpus/` on purpose:
  `harness.py` auto-iterates every `corpus/*` subdirectory.
- `eval_c4.py` — deterministic bits/char of all five scheme variants on C4
  with per-cell round-trip verification. Output: `data/results_c4.csv`
  (byte-identical across machines; verified).

## Telemetry serialization, AEAD cost and radio time

- `eval_cbor.py` measures CBOR (RFC 8949) as a telemetry baseline on C2 and
  C5 - as a self-describing map, as a schema array, and packed with integer
  timestamps and fixed-point values - alone and with the encoders applied on
  top (artifacts retrained on the CBOR form). Output: `results_cbor.csv`.
- `bench_aead.c` times AES-256-GCM (OpenSSL EVP, 96-bit nonce, 16-byte tag)
  in C at the exact encoded size of every scheme and cell, so the AEAD share
  of the per-message cost is measured, not inferred. Output: `results_aead.csv`.
  Needs `libssl-dev`.
- `bench_kem.c` times ML-KEM-512/768/1024 key generation, encapsulation and
  decapsulation through the liboqs C API, with the same clock and protocol as
  the codec and AEAD drivers. It is built against the same liboqs shared
  library that the Python binding loads (or `$OQS_INSTALL_PATH`, or
  `~/_oqs`). Output: `results_kem_c.csv`, `summary_kem_c.txt`. To add these
  timings to an existing results directory without rerunning everything:
  `python3 run_all.py --kem-c-only <results dir>`.
- `lora_airtime.py` converts the measured payload sizes into LoRaWAN EU868
  time on air (Semtech LoRa modem formula, 125 kHz, CR 4/5, SF7 and SF12,
  13-byte LoRaWAN framing, frames split at the data rate's maximum payload),
  including the 768-byte ML-KEM-512 ciphertext sent once per session. This is
  a calculation from payload sizes, not an energy measurement.
  Output: `results_airtime.csv`.
