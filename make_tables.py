"""
make_tables.py - Print the paper's tables (Markdown) from a results dir.

Usage:  python3 make_tables.py --resdir results_phase3/<timestamp>
Reads:  results_pipeline.csv, results_c_latency.csv, results_c_sizes.csv,
        results_kem.csv, results_c4.csv / results_c5.csv / results_dict_size.csv
        (optional)

results_c_latency.csv holds every scheme timed from C (bench / bench_lib)
on 5 independent input slices per (corpus, length); results_c_sizes.csv the
encoded size of every slice. A cell's latency is the median over slices of
the per-slice median over repetitions; ranges are min-max over slices.

Tables produced:
  T2   bits/char, every scheme x corpus x length (deterministic)
  T2b  same for C4 (chat register); T2c same for C5 (real sensor readings)
  T3   latency at 20,000 chars, every scheme timed in C, with slice ranges
  T3e  latency at 120 and 500 chars, every scheme timed in C
  T3d  S2 decode by decoder implementation: decode/encode and S2/S1 ratios
  TA   AES-256-GCM per message in C at each scheme's payload size
  TC   CBOR (map / array / packed) alone and with the encoders, C2 and C5
  TL   LoRaWAN EU868 time on air implied by the payload sizes (calculation)
  XS   crossover robustness: on how many of the 5 slices each scheme's
       payload is smaller than S2-dom's; XR bits/char ranges over slices
  T6   ML-KEM-512 session costs
  T8   wire bytes per single-message session on C1 (@500 and @20,000)
  TD   dictionary-size sensitivity (if results_dict_size.csv present)
  XO   crossover report: smallest length at which each zstd variant
       beats S2-dom, per corpus
"""

import argparse
import csv
import pathlib
import statistics as st
from collections import defaultdict

SCHEMES = ["S1-6bit", "S2-dom", "S2-en", "S3-zstd", "S3d-dom", "S3d-en", "S4-utf8", "S5-hs8", "S5-hs11", "S6-unishox"]
C_TIMED = ["S1-6bit", "S2-dom-bit", "S2-dom-table", "S3-zstd", "S3d-dom", "S4-utf8", "S5-hs8", "S5-hs11", "S6-unishox"]
OVERHEAD = 768 + 12 + 16   # ML-KEM-512 ct + nonce + tag


def load(path):
    if not pathlib.Path(path).exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def md(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resdir", default="results")
    R = pathlib.Path(ap.parse_args().resdir)

    pipe = load(R / "results_pipeline.csv")
    bpc, na = {}, set()
    pyt = defaultdict(list)
    for r in pipe:
        key = (r["corpus"], int(r["length"]), r["scheme"])
        if r["status"] == "NA_alphabet":
            na.add(key); continue
        if r["status"] != "ok":
            continue
        bpc[key] = 8 * int(r["encoded_bytes"]) / int(r["length"])
        pyt[key].append((int(r["t_encode_ns"]), int(r["t_decode_ns"])))
    corpora = sorted({k[0] for k in bpc})
    lengths = sorted({k[1] for k in bpc})

    def cell(c, n, s):
        if (c, n, s) in na: return "N/A"
        v = bpc.get((c, n, s)); return f"{v:.3f}" if v is not None else "-"

    print("## T2 - bits per character (deterministic)\n")
    rows = [[c.upper(), f"{n:,}"] + [cell(c, n, s) for s in SCHEMES]
            for c in corpora for n in lengths if any((c, n, s) in bpc for s in SCHEMES)]
    print(md(["Corpus", "Length"] + SCHEMES, rows))

    for cx, title in (("c4", "C4 (chat register)"), ("c5", "C5 (real sensor readings, Intel Lab)")):
        ex = load(R / f"results_{cx}.csv")
        if not ex:
            continue
        print(f"## T2{'b' if cx == 'c4' else 'c'} - {title} bits per character\n")
        g = {(int(r["length"]), r["scheme"]): float(r["bpc"]) for r in ex}
        ls = sorted({k[0] for k in g})
        rows = [[cx.upper(), f"{n:,}"] + [f"{g[(n, s)]:.3f}" if (n, s) in g else "-" for s in SCHEMES] for n in ls]
        print(md(["Corpus", "Length"] + SCHEMES, rows))
        drops = {int(r["length"]): int(r["s1_dropped"]) for r in ex if r["scheme"] == "S1-6bit"}
        print(f"S1 drop rate on {cx.upper()}: " + ", ".join(f"{n:,}: {100*d/n:.2f}%" for n, d in sorted(drops.items())) + "\n")
        for s in ("S3-zstd", "S3d-dom", "S5-hs8", "S5-hs11", "S6-unishox"):
            wins = [n for n in ls if g[(n, s)] < g[(n, "S2-dom")]]
            print(f"- {cx.upper()} {s} beats S2-dom at: {', '.join(f'{n:,}' for n in wins) or 'no tested length'}")
        print()

    lat = load(R / "results_c_latency.csv")
    if lat:
        per = defaultdict(list)
        for r in lat:
            per[(r["corpus"], int(r["length"]), r["scheme"], r["op"], int(r["slice"]))].append(int(r["ns"]) / 1e3)
        cellv = defaultdict(list)
        for (c, n, s, op, k), v in per.items():
            cellv[(c, n, s, op)].append(st.median(v))
        nslices = max(int(r["slice"]) for r in lat) + 1
        nreps = max(int(r["rep"]) for r in lat) + 1

        def med(c, n, s, op):
            v = cellv.get((c, n, s, op)); return st.median(v) if v else None

        def rng(c, n, s, op):
            v = cellv[(c, n, s, op)]; return min(v), max(v)

        print(f"## T3 - latency at 20,000 chars, all schemes timed in C (us; median over {nslices} slices "
              f"of per-slice medians over {nreps} reps; [min-max over slices])\n")
        rows = []
        for c in corpora:
            for s in C_TIMED:
                e = med(c, 20000, s, "encode")
                if e is None: continue
                d = med(c, 20000, s, "decode")
                (e0, e1), (d0, d1) = rng(c, 20000, s, "encode"), rng(c, 20000, s, "decode")
                rows.append([c.upper(), s, f"{e:.1f} [{e0:.1f}-{e1:.1f}]", f"{d:.1f} [{d0:.1f}-{d1:.1f}]"])
        print(md(["Corpus", "Scheme", "Encode", "Decode"], rows))

        print("## T3e - latency at 120 and 500 chars (us; median over slices)\n")
        rows = []
        for c in corpora:
            for s in C_TIMED:
                if med(c, 120, s, "encode") is None: continue
                rows.append([c.upper(), s] + [f"{med(c, n, s, op):.2f}" for n in (120, 500) for op in ("encode", "decode")])
        print(md(["Corpus", "Scheme", "Enc @120", "Dec @120", "Enc @500", "Dec @500"], rows))

        print("## T3d - S2 decode by decoder implementation (us; median over slices; ratios)\n")
        rows = []
        for c in corpora:
            for n in lengths:
                e = med(c, n, "S2-dom-table", "encode"); db = med(c, n, "S2-dom-bit", "decode"); dl = med(c, n, "S2-dom-table", "decode")
                d1 = med(c, n, "S1-6bit", "decode")
                if e is None or db is None or dl is None: continue
                rows.append([c.upper(), f"{n:,}", f"{e:.2f}", f"{db:.2f}", f"{dl:.2f}", f"{dl/e:.1f}x", f"{db/dl:.1f}x",
                             f"{db/d1:.1f}x" if d1 else "N/A", f"{dl/d1:.1f}x" if d1 else "N/A"])
        print(md(["Corpus", "Length", "S2 encode", "S2 dec (bit)", "S2 dec (table)", "dec/enc (table)",
                  "bit/table", "S2/S1 dec (bit)", "S2/S1 dec (table)"], rows))
        for lab, s in (("bit-serial", "S2-dom-bit"), ("table-driven", "S2-dom-table")):
            v = [med(c, n, s, "decode") / med(c, n, "S1-6bit", "decode") for c in corpora for n in lengths
                 if med(c, n, "S1-6bit", "decode") and med(c, n, s, "decode")]
            if v: print(f"S2/S1 decode ratio ({lab}) over corpora and lengths: {min(v):.1f}-{max(v):.1f}x")
        print()

    sizes = load(R / "results_c_sizes.csv")
    if sizes:
        sz = {(r["corpus"], int(r["length"]), int(r["slice"]), r["scheme"]): int(r["encoded_bytes"]) for r in sizes}
        ks = sorted({k[2] for k in sz})
        print(f"## XS - crossover robustness: slices (of {len(ks)}) on which a scheme's payload is "
              "smaller than S2-dom's\n")
        rows = []
        for c in corpora:
            for n in lengths:
                if (c, n, 0, "S2-dom-bit") not in sz: continue
                row = [c.upper(), f"{n:,}"]
                for s in ("S3-zstd", "S3d-dom", "S5-hs8", "S5-hs11", "S6-unishox"):
                    row.append(sum(sz[(c, n, k, s)] < sz[(c, n, k, "S2-dom-bit")] for k in ks))
                rows.append(row)
        print(md(["Corpus", "Length", "S3-zstd", "S3d-dom", "S5-hs8", "S5-hs11", "S6-unishox"], rows))
        print("## XR - bits/char range over slices (min-max)\n")
        rows = []
        for c in corpora:
            for n in lengths:
                if (c, n, 0, "S2-dom-bit") not in sz: continue
                row = [c.upper(), f"{n:,}"]
                for s in ("S2-dom-bit", "S3-zstd", "S3d-dom", "S5-hs8", "S6-unishox"):
                    v = [8 * sz[(c, n, k, s)] / n for k in ks]
                    row.append(f"{min(v):.3f}-{max(v):.3f}")
                rows.append(row)
        print(md(["Corpus", "Length", "S2-dom", "S3-zstd", "S3d-dom", "S5-hs8", "S6-unishox"], rows))

    aead = load(R / "results_aead.csv")
    if aead:
        print("## TA - AES-256-GCM per message, timed in C (us; median over reps), next to the codec cost\n")
        g = defaultdict(list)
        for r in aead:
            g[(r["corpus"], int(r["length"]), r["scheme"], r["op"])].append(int(r["ns"]) / 1e3)
        bsz = {(r["corpus"], int(r["length"]), r["scheme"]): int(r["payload_bytes"]) for r in aead}
        rows = []
        for c in ("c1", "c2"):
            for n in (120, 500, 20000):
                for s in ("S4-utf8", "S2-dom-table", "S3d-dom", "S6-unishox"):
                    if (c, n, s, "encrypt") not in g: continue
                    rows.append([c.upper(), f"{n:,}", s, f"{bsz[(c, n, s)]:,}",
                                 f"{st.median(g[(c, n, s, 'encrypt')]):.2f}", f"{st.median(g[(c, n, s, 'decrypt')]):.2f}"])
        print(md(["Corpus", "Length", "Scheme", "Payload B", "AEAD encrypt", "AEAD decrypt"], rows))

    cb = load(R / "results_cbor.csv")
    if cb:
        print("## TC - CBOR serialization alone and with the encoders (mean bytes per record)\n")
        g = {(r["corpus"], int(r["records_per_msg"]), r["format"], r["encoder"]): float(r["mean_bytes_per_record"]) for r in cb}
        chars = {(r["corpus"], int(r["records_per_msg"])): float(r["mean_text_chars"]) for r in cb}
        ks = sorted({(r["corpus"], int(r["records_per_msg"])) for r in cb})
        hdr = ["Format", "Encoder"] + [f"{c.upper()} k={k} (~{chars[(c, k)]:.0f} chars)" for c, k in ks]
        rows = []
        for fmt in ("text", "cbor-map", "cbor-array", "cbor-packed"):
            for e in ("none", "S2-dom", "S3-zstd", "S3d-dom", "S5-hs8"):
                rows.append([fmt, e] + [f"{g[(c, k, fmt, e)]:.1f}" for c, k in ks])
        print(md(hdr, rows))

    air = load(R / "results_airtime.csv")
    if air:
        print("## TL - LoRaWAN EU868 time on air implied by the payload sizes (calculation, ms; frames)\n")
        rows = [[r["corpus"].upper(), r["length"], r["scheme"], f"SF{r['sf']}", r["app_bytes"], r["frames"], r["airtime_ms"]] for r in air]
        print(md(["Corpus", "Length", "Scheme", "SF", "App. bytes", "Frames", "Airtime (ms)"], rows))

    kem = load(R / "results_kem.csv")
    if kem:
        print("## T6 - ML-KEM-512 session costs, us\n")
        rows = []
        for col, lab in [("t_keygen_ns", "KeyGen"), ("t_encaps_ns", "Encaps"), ("t_decaps_ns", "Decaps")]:
            v = [int(r[col]) / 1e3 for r in kem]
            rows.append([lab, f"{st.mean(v):.1f} +/- {st.stdev(v):.1f}", f"{st.median(v):.1f}"])
        print(md(["Operation", "Mean +/- SD", "Median"], rows))
        print(f"pk {kem[0]['pk_bytes']} B, ct {kem[0]['kem_ct_bytes']} B, n = {len(kem)}\n")

    if "c1" in corpora:
        print(f"## T8 - wire bytes per single-message session on C1 (overhead {OVERHEAD} B)\n")
        rows = []
        for s in SCHEMES:
            if (("c1", 500, s) in na) or ("c1", 500, s) not in bpc: continue
            e5 = round(bpc[("c1", 500, s)] * 500 / 8); e20 = round(bpc[("c1", 20000, s)] * 20000 / 8)
            rows.append([s, f"{e5:,}", f"{e5+OVERHEAD:,}", f"{e20:,}", f"{e20+OVERHEAD:,}"])
        print(md(["Scheme", "Encoded @500", "Total @500", "Encoded @20,000", "Total @20,000"], rows))

    ds = load(R / "results_dict_size.csv")
    if ds:
        print("## TD - S3d sensitivity to dictionary size (bits/char)\n")
        sizes = sorted({int(r["dict_size"]) for r in ds if int(r["dict_size"]) > 0})
        g = {(r["corpus"], int(r["length"]), r["config"], int(r["dict_size"])): float(r["bpc"]) for r in ds}
        hdr = ["Corpus", "Length", "S3 (no dict)"] + [f"{cfg}@{s}" for s in sizes for cfg in ("dom", "en")]
        rows = []
        for c in sorted({r["corpus"] for r in ds}):
            for n in sorted({int(r["length"]) for r in ds if r["corpus"] == c}):
                rows.append([c.upper(), f"{n:,}", f"{g[(c, n, 'S3-zstd', 0)]:.3f}"] +
                            [f"{g[(c, n, 'S3d-'+cfg, s)]:.3f}" for s in sizes for cfg in ("dom", "en")])
        print(md(hdr, rows))

    print("## XO - crossover report (smallest length at which scheme beats S2-dom)\n")
    for c in corpora:
        for s in ["S3-zstd", "S3d-dom", "S5-hs8", "S5-hs11", "S6-unishox"]:
            wins = [n for n in lengths if (c, n, s) in bpc and (c, n, "S2-dom") in bpc and bpc[(c, n, s)] < bpc[(c, n, "S2-dom")]]
            losses = [n for n in lengths if (c, n, s) in bpc and (c, n, "S2-dom") in bpc and bpc[(c, n, s)] >= bpc[(c, n, "S2-dom")]]
            if not wins: msg = "never beats S2-dom at tested lengths"
            elif not losses: msg = "beats S2-dom at every tested length"
            else: msg = f"S2-dom leads up to {max(losses):,}; {s} leads from {min(n for n in wins if n > max(losses)) if any(n > max(losses) for n in wins) else min(wins):,}"
            print(f"- {c.upper()} {s}: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
