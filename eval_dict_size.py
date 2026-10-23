"""
eval_dict_size.py - Sensitivity of S3d to the pre-shared dictionary size.

Payload-only (bits/char is deterministic), so this runs identically on
any machine. For every corpus x length and every dictionary size in
SIZES, trains a domain-matched and an English dictionary (same rules as
s3_dict.py) and reports bits/char. The 2048 B column must reproduce the
S3d-dom / S3d-en rows of results_pipeline.csv exactly.

Usage:  python3 eval_dict_size.py [--outdir results]
Output: <outdir>/results_dict_size.csv
        corpus,length,config,dict_size,encoded_bytes,bpc,roundtrip
"""

import argparse
import csv
import pathlib

import zstandard

import s3_dict

SIZES = [256, 512, 1024, 2048, 4096]


def corpus_files():
    out = []
    for cdir in sorted(p for p in pathlib.Path("corpus").iterdir() if p.is_dir()):
        for fp in sorted(cdir.glob("c?_*.txt"), key=lambda p: int(p.stem.split("_")[1])):
            out.append((cdir.name, fp))
    c4 = pathlib.Path("corpus_c4")
    if c4.is_dir():
        for fp in sorted(c4.glob("c4_*.txt"), key=lambda p: int(p.stem.split("_")[1])):
            out.append(("c4", fp))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(exist_ok=True)

    zc0 = zstandard.ZstdCompressor(level=s3_dict.LEVEL)
    rows = []
    files = corpus_files()
    print(f"zstandard {zstandard.__version__}; dict sizes {SIZES}; "
          f"{len(files)} corpus files")
    print(f"{'corpus':<7}{'len':>7}{'S3 (no dict)':>14}" +
          "".join(f"{'dom@'+str(s):>10}{'en@'+str(s):>10}" for s in SIZES))
    dicts_by_size = {s: s3_dict.build_dicts(size=s, outdir=None) for s in SIZES}
    for corpus, fp in files:
        text = fp.read_text(encoding="utf-8")
        n = len(text)
        base = len(zc0.compress(text.encode("utf-8")))
        rows.append({"corpus": corpus, "length": n, "config": "S3-zstd",
                     "dict_size": 0, "encoded_bytes": base,
                     "bpc": round(8 * base / n, 3), "roundtrip": "ok"})
        line = f"{corpus:<7}{n:>7}{8*base/n:>14.3f}"
        for s in SIZES:
            codecs = s3_dict.codecs_for(corpus, dicts_by_size[s])
            for cfg in ("S3d-dom", "S3d-en"):
                blob = codecs[cfg].encode(text)
                ok = codecs[cfg].decode(blob) == text
                rows.append({"corpus": corpus, "length": n, "config": cfg,
                             "dict_size": s, "encoded_bytes": len(blob),
                             "bpc": round(8 * len(blob) / n, 3),
                             "roundtrip": "ok" if ok else "MISMATCH"})
                line += f"{8*len(blob)/n:>10.3f}"
        print(line)
    with open(outdir / "results_dict_size.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    bad = [r for r in rows if r["roundtrip"] != "ok"]
    print(f"\nwrote {outdir/'results_dict_size.csv'} ({len(rows)} rows)  mismatches={len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
