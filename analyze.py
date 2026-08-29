"""
analyze.py — Summarize harness CSVs into the paper's core tables.

Reads results/results_pipeline.csv and results/results_kem.csv, prints:
  1. KEM session costs (mean±SD over reps) — Background/Results headline
  2. Per (corpus, length, scheme): bits/char, encode/decode latency mean±SD
No external deps (statistics stdlib) so it runs anywhere the harness does.
"""

import argparse
import csv
import pathlib
import statistics as st
from collections import defaultdict

_ap = argparse.ArgumentParser()
_ap.add_argument("--resdir", default="results",
                 help="directory containing results_kem.csv and results_pipeline.csv")
RES = pathlib.Path(_ap.parse_args().resdir)


def load(name):
    with open(RES / name, newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    kem = load("results_kem.csv")
    for col, label in [("t_keygen_ns", "keygen"), ("t_encaps_ns", "encaps"),
                       ("t_decaps_ns", "decaps")]:
        v = [int(r[col]) / 1e3 for r in kem]  # -> microseconds
        print(f"ML-KEM-512 {label:<7} {st.mean(v):8.1f} ± {st.stdev(v):6.1f} µs"
              f"   (n={len(v)})")
    print(f"KEM ciphertext: {kem[0]['kem_ct_bytes']} B (once per session)\n")

    rows = load("results_pipeline.csv")
    groups = defaultdict(list)
    na = set()
    for r in rows:
        key = (r["corpus"], int(r["length"]), r["scheme"])
        if r["status"] == "NA_alphabet":
            na.add(key)
        elif r["status"] == "ok":
            groups[key].append(r)

    print(f"{'corpus':<8}{'len':>7}{'scheme':<9}{'bits/ch':>9}"
          f"{'enc µs (mean±SD)':>20}{'dec µs (mean±SD)':>20}")
    for key in sorted(set(list(groups) + list(na))):
        c, n, s = key
        if key in na:
            print(f"{c:<8}{n:>7}{s:<9}{'N/A':>9}{'—':>20}{'—':>20}")
            continue
        g = groups[key]
        bpc = 8 * int(g[0]["encoded_bytes"]) / n
        te = [int(r["t_encode_ns"]) / 1e3 for r in g]
        td = [int(r["t_decode_ns"]) / 1e3 for r in g]
        print(f"{c:<8}{n:>7}{s:<9}{bpc:>9.3f}"
              f"{st.mean(te):>12.1f} ±{st.stdev(te):>6.1f}"
              f"{st.mean(td):>12.1f} ±{st.stdev(td):>6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
