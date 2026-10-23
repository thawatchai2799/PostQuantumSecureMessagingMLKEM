"""
eval_c4.py - Payload efficiency (bits/char) of every scheme on the
payload-only corpora C4 (chat register) and C5 (real sensor readings).

Deterministic by construction (no timing), so the numbers are
machine-independent and verifiable by re-running anywhere. Scheme
construction mirrors harness.py exactly:
  S1-6bit : mode="drop" (chat text contains lowercase/apostrophes, as C1)
  S2-en   : canonical Huffman trained on s2_train.txt  (C1's English table
            applied CROSS-corpus -> near-domain transfer test)
  S2-dom  : canonical Huffman trained on s2_train_c4.txt (pre-shared,
            held-out same-domain split)
  S3-zstd : zstandard level 3, no dictionary
  S3d-dom : zstd + pre-shared dictionary trained on s2_train_c4.txt
  S3d-en  : zstd + the English dictionary (cross-domain)
  S4-utf8 : UTF-8 passthrough
  S5-hs8  : heatshrink LZSS, 256 B window (MCU-class, stateless)
  S5-hs11 : heatshrink LZSS, 2 KiB window
  S6-unishox : Unishox2 short-string coder (stateless)
For C5 the domain artifacts are trained on s2_train_c5.txt instead.
Round-trip verified per cell (exact for S2*/S3/S4; canonicalized for S1).

Usage:  python3 eval_c4.py [--corpus c4|c5]
Output: results_<corpus>.csv (corpus,length,scheme,encoded_bytes,bpc,
        s1_dropped,roundtrip)
"""

import argparse
import csv
import pathlib

import zstandard

try:
    import s1_sixbit
    import s3_dict
    import s5_heatshrink
    import s6_unishox
    from s2_huffman import HuffmanTable
except ImportError:
    raise SystemExit(
        "ERROR: cannot import the codec modules (s1_sixbit / s2_huffman / s5_heatshrink).\n"
        "If heatshrink2 is missing: pip install heatshrink2\n"
        "Run this from the experiment directory that contains harness.py\n"
        "(e.g. ~/mlkem-encoding-paper/run2), not from Downloads.")

LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="c4", choices=["c4", "c5"])
    c = ap.parse_args().corpus
    print(f"zstandard {zstandard.__version__} | python corpus eval (deterministic) | {c}")
    hint = {"c4": "Run python3 make_c4_modern.py first.",
            "c5": "Run python3 make_c5_intel.py --input data.txt.gz first."}[c]
    for req in ["s2_train.txt", f"s2_train_{c}.txt", f"corpus_{c}/{c}_500.txt"]:
        if not pathlib.Path(req).exists():
            raise SystemExit(f"ERROR: missing {req}.\n" + (hint if c in req else
                             "Run from the experiment directory that contains harness.py."))
    en = HuffmanTable.train(pathlib.Path("s2_train.txt").read_bytes())
    dom = HuffmanTable.train(pathlib.Path(f"s2_train_{c}.txt").read_bytes())
    zc = zstandard.ZstdCompressor(level=3)
    zd = zstandard.ZstdDecompressor()
    d3 = s3_dict.codecs_for(c, s3_dict.build_dicts())
    hs = s5_heatshrink.codecs()
    ux = s6_unishox.UnishoxCodec()

    schemes = {
        "S1-6bit": (lambda t: s1_sixbit.encode(t, mode="drop"),
                    s1_sixbit.decode,
                    lambda t: s1_sixbit.canonicalize(t, "drop")[0]),
        "S2-en":   (en.encode, en.decode, lambda t: t),
        "S2-dom":  (dom.encode, dom.decode, lambda t: t),
        "S3-zstd": (lambda t: zc.compress(t.encode("utf-8")),
                    lambda b: zd.decompress(b).decode("utf-8"),
                    lambda t: t),
        "S3d-dom": (d3["S3d-dom"].encode, d3["S3d-dom"].decode, lambda t: t),
        "S3d-en":  (d3["S3d-en"].encode, d3["S3d-en"].decode, lambda t: t),
        "S4-utf8": (lambda t: t.encode("utf-8"),
                    lambda b: b.decode("utf-8"),
                    lambda t: t),
        "S5-hs8":  (hs["S5-hs8"].encode, hs["S5-hs8"].decode, lambda t: t),
        "S5-hs11": (hs["S5-hs11"].encode, hs["S5-hs11"].decode, lambda t: t),
        "S6-unishox": (ux.encode, ux.decode, lambda t: t),
    }

    rows = []
    for n in LENGTHS:
        text = pathlib.Path(f"corpus_{c}/{c}_{n}.txt").read_text(encoding="utf-8")
        assert len(text) == n
        for name, (enc, dec, ref) in schemes.items():
            blob = enc(text)
            ok = dec(blob) == ref(text)
            dropped = s1_sixbit.canonicalize(text, "drop")[1] if name == "S1-6bit" else 0
            rows.append({
                "corpus": c, "length": n, "scheme": name,
                "zstandard_version": zstandard.__version__,
                "encoded_bytes": len(blob),
                "bpc": round(8 * len(blob) / n, 3),
                "s1_dropped": dropped,
                "roundtrip": "ok" if ok else "MISMATCH",
            })
            print(f"{c} {n:6d} {name:10s} {len(blob):6d} B  "
                  f"{8*len(blob)/n:6.3f} bits/char  rt={'ok' if ok else 'MISMATCH'}"
                  + (f"  dropped={dropped} ({100*dropped/n:.2f}%)" if name == "S1-6bit" else ""))

    bad = [r for r in rows if r["roundtrip"] != "ok"]
    with open(f"results_{c}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote results_{c}.csv ({len(rows)} rows)  mismatches={len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
