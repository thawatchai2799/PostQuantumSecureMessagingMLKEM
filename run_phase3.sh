#!/usr/bin/env bash
# run_phase3.sh — Phase 3 orchestrator. Runs the entire experiment.
#
# Usage:   ./run_phase3.sh [REPS]        (default REPS=50)
# Output:  results_phase3/<timestamp>/   all CSVs + footprint + logs
#
# Stages (each logged; failures abort with a clear message):
#   0. build          gcc codec + bench, generate s2_tables.h
#   1. corpus         C1 from Gutenberg (download or --input), C2, C3
#   2. verify         Python round-trip suite + C byte-exact suite
#   3. kem+aead       Python harness: KEM session costs, AEAD, sizes (CSV)
#   4. c-latency      C bench: encode/decode ns for every scheme x file (CSV)
#   5. footprint      x86 -Os codec size; ARM cross size if toolchain present
#   6. summarize      analyze.py + c-latency summary
set -euo pipefail
REPS="${1:-50}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="results_phase3/$TS"
mkdir -p "$OUT"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }

log "=== Stage 0: build (REPS=$REPS) ==="
# English pre-shared training text (Gutenberg #161, disjoint from C1 = #1342).
# Set EN_TRAIN_INPUT=/path/to/pg161.txt to use a local copy (no network).
if [ ! -f s2_train_PROVENANCE.txt ]; then
    python3 make_s2_train_en.py ${EN_TRAIN_INPUT:+--input "$EN_TRAIN_INPUT"} | tee -a "$OUT/run.log"
fi
cp s2_train*.txt "$OUT/"
python3 gen_s2_tables.py | tee -a "$OUT/run.log"
gcc -O2 -Wall -o bench codec.c bench_main.c
gcc -Os -c codec.c -o codec_os.o

log "=== Stage 1: corpus ==="
# Regenerate C1 if any slice (incl. the newer 120/2000/3000 lengths) is missing.
# Set C1_INPUT=/path/to/pg1342.txt to use a local Gutenberg copy (no network).
if [ ! -f corpus/c1/c1_120.txt ] || [ ! -f corpus/c1/c1_3000.txt ]; then
    python3 make_c1_english.py ${C1_INPUT:+--input "$C1_INPUT"}
fi
python3 make_c2_iot.py    >/dev/null
python3 make_c3_uniform.py >/dev/null
python3 make_c4_modern.py  >/dev/null
python3 s3_dict.py | tee -a "$OUT/run.log"      # pre-shared zstd dictionaries
cp -r s3_dicts "$OUT/s3_dicts"
cp -r corpus "$OUT/corpus-snapshot"

log "=== Stage 2: verify (abort on any failure) ==="
python3 test_roundtrip.py > "$OUT/verify_python.txt"
tail -1 "$OUT/verify_python.txt"
grep -q "PASS: 0" "$OUT/verify_python.txt"
python3 verify_c.py | tee "$OUT/verify_c.txt"
grep -q "52/52\|/.* passed" "$OUT/verify_c.txt"

log "=== Stage 3: KEM session costs + AEAD pipeline (Python harness) ==="
python3 harness.py --reps "$REPS" --warmup 5 --outdir "$OUT" \
    | tee -a "$OUT/run.log"

log "=== Stage 4: C codec latency (fair C-vs-C numbers) ==="
CSV="$OUT/results_c_latency.csv"
echo "corpus,length,scheme,op,rep,ns" > "$CSV"
for cdir in corpus/c1 corpus/c2 corpus/c3; do
    c=$(basename "$cdir")
    case "$c" in c1) S2=s2en;; c2) S2=s2c2;; c3) S2=s2c3;; esac
    for f in "$cdir"/c?_*.txt; do
        len=$(basename "$f" .txt); len=${len##*_}
        schemes="$S2"
        [ "$c" != "c2" ] && schemes="s1 $S2"     # policy (c): S1 N/A on c2
        for sch in $schemes; do
            ./bench time "$sch" "$f" "$REPS" | tail -n +2 \
                | awk -F, -v c="$c" -v l="$len" -v s="$sch" \
                      '{print c","l","s","$1","$2","$3}' >> "$CSV"
        done
    done
done
log "c-latency rows: $(($(wc -l < "$CSV") - 1))"

log "=== Stage 5: footprint ==="
{ echo "== x86 -Os codec-only =="; size codec_os.o; } > "$OUT/footprint.txt"
if command -v arm-none-eabi-gcc >/dev/null 2>&1; then
    arm-none-eabi-gcc -Os -mcpu=cortex-m4 -c codec.c -o codec_arm.o
    { echo; echo "== ARM Cortex-M4 -Os codec-only =="; \
      arm-none-eabi-size codec_arm.o; } >> "$OUT/footprint.txt"
    log "ARM footprint collected"
else
    echo "(ARM toolchain absent — install gcc-arm-none-eabi and rerun stage 5)" \
        >> "$OUT/footprint.txt"
    log "WARN: no ARM toolchain; x86 footprint only"
fi
cat "$OUT/footprint.txt"

log "=== Stage 6: summaries ==="
( cd "$OUT" && python3 - <<'PYEOF'
import csv, statistics as st
from collections import defaultdict
g = defaultdict(list)
with open("results_c_latency.csv") as f:
    for r in csv.DictReader(f):
        g[(r["corpus"], int(r["length"]), r["scheme"], r["op"])].append(int(r["ns"]))
print(f"{'corpus':<6}{'len':>7}{'scheme':<7}{'op':<8}{'mean µs':>9}{'SD':>8}{'median':>9}")
for k in sorted(g):
    v = [x/1e3 for x in g[k]]
    print(f"{k[0]:<6}{k[1]:>7}{k[2]:<7}{k[3]:<8}{st.mean(v):>9.1f}{st.stdev(v):>8.1f}{st.median(v):>9.1f}")
PYEOF
) | tee "$OUT/summary_c_latency.txt"
python3 analyze.py --resdir "$OUT" > "$OUT/summary_pipeline.txt"
python3 eval_c4.py > "$OUT/summary_c4.txt" && mv results_c4.csv "$OUT/"
python3 eval_dict_size.py --outdir "$OUT" | tee "$OUT/summary_dict_size.txt"
python3 make_tables.py --resdir "$OUT" > "$OUT/paper_tables.md"

log "=== Stage 7: output integrity verification ==="
python3 - "$OUT" "$REPS" <<'PYEOF'
# Guard against stale-artifact contamination: every CSV in this run's
# output directory must contain exactly REPS repetitions.
import csv, sys, pathlib
out, reps = pathlib.Path(sys.argv[1]), int(sys.argv[2])
def reps_in(path, col="rep"):
    with open(path, newline="") as f:
        vals = {int(r[col]) for r in csv.DictReader(f) if r.get(col, "").lstrip("-").isdigit() and int(r[col]) >= 0}
    return (min(vals), max(vals), len(vals)) if vals else (None, None, 0)
fail = False
for name in ["results_kem.csv", "results_pipeline.csv", "results_c_latency.csv"]:
    lo, hi, n = reps_in(out / name)
    ok = (lo == 0 and hi == reps - 1 and n == reps)
    print(f"  {name}: reps 0..{hi} ({n} distinct) -> {'OK' if ok else 'STALE/INCOMPLETE'}")
    fail |= not ok
sys.exit(1 if fail else 0)
PYEOF

log "=== DONE — everything in $OUT ==="
