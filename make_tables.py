"""
make_tables.py - Print the paper's tables (Markdown) from a results dir.

Usage:  python3 make_tables.py --resdir results_phase3/<timestamp>
Reads:  results_pipeline.csv, results_c_latency.csv, results_kem.csv,
        results_c4.csv (optional), results_dict_size.csv (optional)

Tables produced:
  T2   bits/char, every scheme x corpus x length (deterministic)
  T2b  same for C4 (if results_c4.csv present)
  T3   C-codec latency at 20,000 chars (mean+/-SD, median) for S1 / S2-dom
       (S2-dom decode with the bit-serial and the table-driven decoder)
  T3d  decode-asymmetry report: S2 decode/encode and S2/S1 decode ratios per
       corpus and length, for both decoders
  T3b  Python-side latency for S3 / S3d-dom / S4 at 20,000 chars (median)
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

SCHEMES = ["S1-6bit", "S2-dom", "S2-en", "S3-zstd", "S3d-dom", "S3d-en", "S4-utf8", "S5-hs8", "S5-hs11"]
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

    c4 = load(R / "results_c4.csv")
    if c4:
        print("## T2b - C4 bits per character\n")
        g = {(int(r["length"]), r["scheme"]): float(r["bpc"]) for r in c4}
        ls = sorted({k[0] for k in g})
        rows = [["C4", f"{n:,}"] + [f"{g[(n, s)]:.3f}" if (n, s) in g else "-" for s in SCHEMES] for n in ls]
        print(md(["Corpus", "Length"] + SCHEMES, rows))
        drops = {int(r["length"]): int(r["s1_dropped"]) for r in c4 if r["scheme"] == "S1-6bit"}
        print("S1 drop rate on C4: " + ", ".join(f"{n:,}: {100*d/n:.2f}%" for n, d in sorted(drops.items())) + "\n")

    lat = load(R / "results_c_latency.csv")
    if lat:
        print("## T3 - C-codec latency at 20,000 chars, us (n = reps)\n")
        g = defaultdict(list)
        for r in lat:
            if int(r["length"]) == 20000:
                g[(r["corpus"], r["scheme"], r["op"])].append(int(r["ns"]) / 1e3)
        name = {"s1": "S1-6bit", "s2en": "S2-dom (bit-serial dec.)", "s2c2": "S2-dom (bit-serial dec.)",
                "s2c3": "S2-dom (bit-serial dec.)", "s2en-lut": "S2-dom (table-driven dec.)",
                "s2c2-lut": "S2-dom (table-driven dec.)", "s2c3-lut": "S2-dom (table-driven dec.)"}
        rows = []
        for c in corpora:
            for sch in ["s1", "s2en", "s2c2", "s2c3", "s2en-lut", "s2c2-lut", "s2c3-lut"]:
                e, d = g.get((c, sch, "encode")), g.get((c, sch, "decode"))
                if not e: continue
                rows.append([c.upper(), name[sch],
                             f"{st.mean(e):.1f} +/- {st.stdev(e):.1f}", f"{st.median(e):.1f}",
                             f"{st.mean(d):.1f} +/- {st.stdev(d):.1f}", f"{st.median(d):.1f}"])
        print(md(["Corpus", "Scheme", "Encode mean +/- SD", "Encode median", "Decode mean +/- SD", "Decode median"], rows))
        for suffix, lab in [("", "bit-serial"), ("-lut", "table-driven")]:
            ratios = []
            for c in corpora:
                s1 = g.get((c, "s1", "decode"))
                s2 = next((g[(c, k + suffix, "decode")] for k in ["s2en", "s2c2", "s2c3"] if (c, k + suffix, "decode") in g), None)
                if s1 and s2: ratios.append(f"{c.upper()}: {st.median(s2)/st.median(s1):.1f}x")
            print(f"Decode median ratio S2/S1 ({lab} decoder): " + ", ".join(ratios) + "\n")

        print("## T3d - S2 decode asymmetry per length (median us; ratios)\n")
        gl = defaultdict(list)
        for r in lat:
            gl[(r["corpus"], int(r["length"]), r["scheme"], r["op"])].append(int(r["ns"]) / 1e3)
        rows = []
        for c in corpora:
            s2 = {"c1": "s2en", "c2": "s2c2", "c3": "s2c3"}[c]
            for n in lengths:
                e = gl.get((c, n, s2, "encode")); db = gl.get((c, n, s2, "decode")); dl = gl.get((c, n, s2 + "-lut", "decode"))
                d1 = gl.get((c, n, "s1", "decode"))
                if not (e and db and dl): continue
                me, mb, ml = st.median(e), st.median(db), st.median(dl)
                rows.append([c.upper(), f"{n:,}", f"{me:.2f}", f"{mb:.2f}", f"{ml:.2f}",
                             f"{mb/me:.1f}x", f"{ml/me:.1f}x", f"{mb/ml:.1f}x",
                             f"{mb/st.median(d1):.1f}x" if d1 else "-", f"{ml/st.median(d1):.1f}x" if d1 else "-"])
        print(md(["Corpus", "Length", "S2 encode", "S2 decode (bit)", "S2 decode (table)",
                  "dec/enc (bit)", "dec/enc (table)", "bit/table", "S2/S1 dec (bit)", "S2/S1 dec (table)"], rows))

    print("## T3b - Python-side latency at 20,000 chars, us (median encode / decode)\n")
    rows = []
    for c in corpora:
        for s in ["S3-zstd", "S3d-dom", "S3d-en", "S4-utf8", "S5-hs8", "S5-hs11"]:
            v = pyt.get((c, 20000, s))
            if v:
                rows.append([c.upper(), s, f"{st.median(x[0] for x in v)/1e3:.1f}", f"{st.median(x[1] for x in v)/1e3:.1f}"])
    print(md(["Corpus", "Scheme", "Encode median", "Decode median"], rows))

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
        for s in ["S3-zstd", "S3d-dom", "S5-hs8", "S5-hs11"]:
            wins = [n for n in lengths if (c, n, s) in bpc and (c, n, "S2-dom") in bpc and bpc[(c, n, s)] < bpc[(c, n, "S2-dom")]]
            losses = [n for n in lengths if (c, n, s) in bpc and (c, n, "S2-dom") in bpc and bpc[(c, n, s)] >= bpc[(c, n, "S2-dom")]]
            if not wins: msg = "never beats S2-dom at tested lengths"
            elif not losses: msg = "beats S2-dom at every tested length"
            else: msg = f"S2-dom leads up to {max(losses):,}; {s} leads from {min(n for n in wins if n > max(losses)) if any(n > max(losses) for n in wins) else min(wins):,}"
            print(f"- {c.upper()} {s}: {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
