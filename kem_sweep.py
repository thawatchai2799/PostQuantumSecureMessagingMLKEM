#!/usr/bin/env python3
"""KEM parameter-set sweep: ML-KEM-512 / 768 / 1024.

Same methodology as the KEM block in harness.py (kept byte-for-byte
identical on purpose, so the -512 row here should reproduce the
original results_kem.csv within noise):
  - liboqs Python binding (oqs.KeyEncapsulation)
  - time.perf_counter_ns(), one call at a time
  - untimed warm-up iterations (default 5) + REPS timed repetitions
  - keygen / encaps / decaps timed separately per repetition

Purpose: demonstrate that the paper's scheme-comparison conclusions
are parameter-set-independent -- only the constant per-session KEM
cost changes; the encoding-scheme comparison happens after Encaps
returns and never touches ML-KEM's internals.

Output: results_kem_sweep.csv (rep, param_set, t_keygen_ns, t_encaps_ns,
        t_decaps_ns, pk_bytes, kem_ct_bytes)
"""
import argparse
import csv
import pathlib
import time

try:
    import oqs
except ImportError:
    raise SystemExit(
        "ERROR: cannot import 'oqs'. Run this from the same Python environment\n"
        "used for harness.py (the one where 'python3 harness.py' worked).")

PARAM_SETS = ["ML-KEM-512", "ML-KEM-768", "ML-KEM-1024"]
# FIPS 203 Table 3: (ek, c) byte lengths per parameter set
FIPS203_SIZES = {"ML-KEM-512": (800, 768),
                 "ML-KEM-768": (1184, 1088),
                 "ML-KEM-1024": (1568, 1568)}


def bench_one(name: str, reps: int, warmup: int) -> list[dict]:
    for _ in range(warmup):
        k = oqs.KeyEncapsulation(name)
        pk = k.generate_keypair()
        ct, ss = k.encap_secret(pk)
        k.decap_secret(ct)

    rows = []
    for rep in range(reps):
        k = oqs.KeyEncapsulation(name)
        t0 = time.perf_counter_ns()
        pk = k.generate_keypair()
        t1 = time.perf_counter_ns()
        ct, ss = k.encap_secret(pk)
        t2 = time.perf_counter_ns()
        ss2 = k.decap_secret(ct)
        t3 = time.perf_counter_ns()
        assert ss == ss2
        assert (len(pk), len(ct)) == FIPS203_SIZES[name], (
            f"{name}: (pk,ct)=({len(pk)},{len(ct)}) != FIPS 203 "
            f"{FIPS203_SIZES[name]} -- wrong or corrupted liboqs build?")
        rows.append({
            "rep": rep,
            "param_set": name,
            "liboqs_version": oqs.oqs_version(),
            "t_keygen_ns": t1 - t0,
            "t_encaps_ns": t2 - t1,
            "t_decaps_ns": t3 - t2,
            "pk_bytes": len(pk),
            "kem_ct_bytes": len(ct),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--allow-partial", action="store_true",
                    help="run whatever parameter sets are enabled instead of failing")
    args = ap.parse_args()
    if args.reps < 1:
        ap.error("--reps must be >= 1")

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    enabled = [m for m in PARAM_SETS if oqs.is_kem_enabled(m)]
    missing = [m for m in PARAM_SETS if m not in enabled]
    print(f"liboqs {oqs.oqs_version()} | enabled: {enabled}")
    if missing and not args.allow_partial:
        loaded = "unknown"
        try:
            with open("/proc/self/maps") as m:
                hits = sorted({ln.split()[-1] for ln in m if "liboqs" in ln})
            if hits:
                loaded = hits[0]
        except OSError:
            pass
        print(f"\nERROR: this liboqs build is missing: {missing}")
        print(f"The library actually loaded by Python is:\n    {loaded}")
        print("(liboqs-python prefers its private copy in ~/_oqs over system installs,")
        print(" so 'make install' alone will NOT fix this. Replace the loaded file.)")
        print("Rebuild full and overwrite the loaded library:")
        print("  git clone --depth 1 --branch 0.16.0 https://github.com/open-quantum-safe/liboqs")
        print("  cmake -S liboqs -B liboqs/build -DBUILD_SHARED_LIBS=ON")
        print("  cmake --build liboqs/build -j$(nproc)")
        if loaded != "unknown":
            print(f"  cp liboqs/build/lib/liboqs.so* $(dirname {loaded})/")
        else:
            print("  # locate the loaded copy first:")
            print("  python3 -c \"import oqs,os;print(open('/proc/self/maps').read())\" | grep liboqs")
            print("  cp liboqs/build/lib/liboqs.so* <that directory>/")
        print("Then re-run this script; the first line must list all three ML-KEM sets.")
        print("(Or pass --allow-partial to proceed with what is enabled.)")
        return 2

    all_rows = []
    for name in enabled:
        print(f"benchmarking {name}  (warmup={args.warmup}, reps={args.reps}) ...")
        rows = bench_one(name, args.reps, args.warmup)
        all_rows.extend(rows)
        ks = sorted(r["t_keygen_ns"] for r in rows)
        es = sorted(r["t_encaps_ns"] for r in rows)
        ds = sorted(r["t_decaps_ns"] for r in rows)
        med = lambda xs: xs[len(xs) // 2] / 1e3
        print(f"  pk={rows[0]['pk_bytes']}B  ct={rows[0]['kem_ct_bytes']}B  "
              f"keygen_med={med(ks):.1f}us  encaps_med={med(es):.1f}us  "
              f"decaps_med={med(ds):.1f}us")

    if not all_rows:
        print("ERROR: no parameter sets were benchmarked; nothing to write.")
        return 2
    out_path = outdir / "results_kem_sweep.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_rows[0].keys())
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nwrote {out_path}  ({len(all_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
