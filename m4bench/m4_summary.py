"""
m4_summary.py - Summarize one or more raw captures of the on-device benchmark.

Reads the text written by m4_capture.py (or a terminal log of the same run),
checks that every record passed its round-trip and size checks, and writes:

  results_m4.csv      every cell record, one row per repetition
  results_m4_kem.csv  every ML-KEM record
  summary_m4.csv      per (corpus, length, scheme): median cycles and
                      microseconds at the reported core clock, maximum stack
                      bytes, encoded bytes, and the host's slice-0 median in
                      microseconds when data/results_c_latency.csv is present
  summary_m4.txt      the same as readable tables, plus the KEM figures, the
                      measurement overhead (null call) and the RAM accounting

Usage (from the repository root):
    python3 m4bench/m4_summary.py <raw capture> [more captures] [--outdir data]
With several captures (for example the -O2 and -Os builds, or two boards),
each is summarized separately; output names carry the device, the build
and the capture file's name.
"""

import argparse
import csv
import pathlib
import statistics as st
from collections import defaultdict

DEV = {1057: "STM32F446", 1073: "STM32F411", 1059: "STM32F401", 1043: "STM32F405/F407", 0: "unknown"}


def parse(path):
    info, cells, kems, nulls, kem_check, end = {}, [], [], [], None, None
    for raw in pathlib.Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        f = raw.strip().split(",")
        if f[0] == "info" and len(f) >= 3:
            info[f[1]] = ",".join(f[2:])
        elif f[0] == "cell" and len(f) == 12:
            cells.append({"corpus": f[1], "length": int(f[2]), "scheme": f[3], "rep": int(f[4]),
                          "enc_cycles": int(f[5]), "enc_stack": int(f[6]), "dec_cycles": int(f[7]),
                          "dec_stack": int(f[8]), "enc_bytes": int(f[9]), "expected_bytes": int(f[10]),
                          "ok": int(f[11])})
        elif f[0] == "kem" and len(f) == 9:
            kems.append({"rep": int(f[1]), "keygen_cycles": int(f[2]), "keygen_stack": int(f[3]),
                         "encaps_cycles": int(f[4]), "encaps_stack": int(f[5]), "decaps_cycles": int(f[6]),
                         "decaps_stack": int(f[7]), "ok": int(f[8])})
        elif f[0] == "null" and len(f) == 4:
            nulls.append((int(f[2]), int(f[3])))
        elif f[0] == "kem_check" and len(f) == 3:
            kem_check = (f[1], int(f[2]))
        elif f[0] == "end" and len(f) == 3:
            end = (int(f[1]), int(f[2]))
    return info, cells, kems, nulls, kem_check, end


def host_medians(path):
    """slice-0 median latency per (corpus, length, scheme, op) in microseconds"""
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    acc = defaultdict(list)
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if r["slice"] == "0":
                acc[(r["corpus"], int(r["length"]), r["scheme"], r["op"])].append(int(r["ns"]) / 1e3)
    return {k: st.median(v) for k, v in acc.items()}


def summarize(path, host, outdir):
    info, cells, kems, nulls, kem_check, end = parse(path)
    if end is None:
        raise SystemExit(f"{path}: no 'end' line; the capture is incomplete")
    hz = int(info.get("cpu_hz", "0") or 0)
    dev = DEV.get(int(info.get("dev_id", "0") or 0), "dev_id " + info.get("dev_id", "?"))
    opt = info.get("opt", "?")
    tag = "".join(ch if ch.isalnum() else "_" for ch in f"{dev}_{opt.lstrip('-')}_{pathlib.Path(path).stem}")
    us = (lambda c: c / hz * 1e6) if hz else (lambda c: float("nan"))

    bad = [c for c in cells if not c["ok"]] + [k for k in kems if not k["ok"]]
    lines = [f"m4bench summary of {path}", f"device: {dev}; core clock: {hz} Hz; build: {opt}; "
             f"repetitions per cell: {info.get('reps', '?')}",
             f"checks: {end[0]} passed, {end[1]} failed" + ("" if not bad else "  <-- FAILURES, see results_m4.csv"),
             f"ML-KEM derandomized vector: {'matches host/liboqs' if kem_check and kem_check[1] else 'MISMATCH or missing'}",
             f"DWT cycle counter running: {info.get('dwt', '?')}; RCC_CFGR={info.get('rcc_cfgr', '?')} "
             f"FLASH_ACR={info.get('flash_acr', '?')} (0 = HSI, prescalers 1, zero wait states)", ""]
    if nulls:
        nc = [n[0] for n in nulls]
        lines.append(f"measurement overhead (empty call): median {st.median(nc):.0f} cycles, "
                     f"stack {max(n[1] for n in nulls)} B")
    lines.append(f"RAM: static .data+.bss of the whole image {info.get('static_bytes', '?')} B "
                 f"(includes the 5 KB input, 2 x 10 KB output buffers and the S1/S2 tables); "
                 f"heatshrink coder state: encoder 2^8 {info.get('hs8_encoder_heap', '?')} B, decoder {info.get('hs8_decoder_heap', '?')} B, "
                 f"encoder 2^11 {info.get('hs11_encoder_heap', '?')} B, decoder {info.get('hs11_decoder_heap', '?')} B")
    lines.append("")

    # per-cell summary
    groups = defaultdict(list)
    for c in cells:
        groups[(c["corpus"], c["length"], c["scheme"])].append(c)
    rows = []
    for (corpus, length, scheme), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        enc = st.median(x["enc_cycles"] for x in g)
        dec = st.median(x["dec_cycles"] for x in g)
        rows.append({"device": dev, "opt": opt, "corpus": corpus, "length": length, "scheme": scheme,
                     "reps": len(g), "enc_cycles_med": int(enc), "enc_cycles_min": min(x["enc_cycles"] for x in g),
                     "enc_cycles_max": max(x["enc_cycles"] for x in g), "enc_us": round(us(enc), 1),
                     "dec_cycles_med": int(dec), "dec_cycles_min": min(x["dec_cycles"] for x in g),
                     "dec_cycles_max": max(x["dec_cycles"] for x in g), "dec_us": round(us(dec), 1),
                     "enc_stack_max": max(x["enc_stack"] for x in g), "dec_stack_max": max(x["dec_stack"] for x in g),
                     "enc_bytes": g[0]["enc_bytes"], "expected_bytes": g[0]["expected_bytes"],
                     "all_ok": int(all(x["ok"] for x in g)),
                     "host_enc_us": round(host.get((corpus, length, scheme, "encode"), float("nan")), 2),
                     "host_dec_us": round(host.get((corpus, length, scheme, "decode"), float("nan")), 2)})
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / f"results_m4_{tag}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cells[0].keys()))
        w.writeheader(); w.writerows(cells)
    with open(outdir / f"results_m4_kem_{tag}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(kems[0].keys()))
        w.writeheader(); w.writerows(kems)
    with open(outdir / f"summary_m4_{tag}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    hdr = f"{'corpus':6s} {'chars':>6s} {'scheme':13s} {'enc cycles':>11s} {'enc us':>8s} {'dec cycles':>11s} {'dec us':>8s} {'stk e':>6s} {'stk d':>6s} {'bytes':>6s} {'host e us':>9s} {'host d us':>9s} ok"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in rows:
        lines.append(f"{r['corpus']:6s} {r['length']:6d} {r['scheme']:13s} {r['enc_cycles_med']:11d} {r['enc_us']:8.1f} "
                     f"{r['dec_cycles_med']:11d} {r['dec_us']:8.1f} {r['enc_stack_max']:6d} {r['dec_stack_max']:6d} "
                     f"{r['enc_bytes']:6d} {r['host_enc_us']:9.2f} {r['host_dec_us']:9.2f} {r['all_ok']}")
    lines.append("")
    if kems:
        lines.append("ML-KEM-512 (mlkem-native C backend), median cycles over repetitions; stack = maximum")
        for op in ("keygen", "encaps", "decaps"):
            cyc = st.median(k[op + "_cycles"] for k in kems)
            lines.append(f"  {op:7s} {int(cyc):10d} cycles  {us(cyc) / 1e3:8.2f} ms  stack {max(k[op + '_stack'] for k in kems):6d} B"
                         f"  (min {min(k[op + '_cycles'] for k in kems)}, max {max(k[op + '_cycles'] for k in kems)})")
    text = "\n".join(lines) + "\n"
    (outdir / f"summary_m4_{tag}.txt").write_text(text, encoding="utf-8")
    print(text)
    return tag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--host-latency", default="data/results_c_latency.csv")
    args = ap.parse_args()
    host = host_medians(args.host_latency)
    for cap in args.captures:
        summarize(cap, host, pathlib.Path(args.outdir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
