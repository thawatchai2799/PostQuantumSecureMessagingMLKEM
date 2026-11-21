"""
artifact_cost.py - What it costs to produce the pre-shared artifacts.

S2 (pre-shared Huffman table) and S3d (pre-shared zstd dictionary) depend on
an artifact that is trained once, off the device, from a training text that is
disjoint from every evaluated slice (see the README: 30,000-character
training texts per domain; the Gutenberg #161 text for English against the
#1342 test corpus; the first 1,000,000 Intel Lab readings held out for C5).
This script times that training step on the host, per domain, as the median
of REPS runs, and records the size of what has to be distributed:

  S2   HuffmanTable.train() on the training bytes -> 257 code lengths (257 B
       to transmit; the device expands them into its ~2 KB tables at start-up,
       see init_cost.c)
  S3d  zstandard.train_dictionary() (zstd's COVER trainer, level 3) on the
       same training text -> 2,048 B dictionary

Nothing here runs on a node: training is a one-time server-side step.

Usage:  python3 artifact_cost.py            (run from the repository root)
Output: artifact_cost.csv  (domain, artifact, training_bytes, samples,
        artifact_bytes, median_ms, min_ms, max_ms)
"""

import csv
import pathlib
import statistics
import sys
import time

import zstandard

from s2_huffman import HuffmanTable
import s3_dict

REPS = 11


def timed(fn):
    runs = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t0) * 1e3)
    return statistics.median(runs), min(runs), max(runs)


def main() -> int:
    rows = []
    for dom, (path, rule) in s3_dict.TRAIN.items():
        p = pathlib.Path(path)
        if not p.exists():
            print(f"{path} missing, {dom} skipped", file=sys.stderr)
            continue
        data = p.read_bytes()
        # S2: Huffman table from byte frequencies of the training text
        med, lo, hi = timed(lambda: HuffmanTable.train(data))
        table = HuffmanTable.train(data)
        rows.append(dict(domain=dom, artifact="S2 Huffman table", training_bytes=len(data), samples=1,
                         artifact_bytes=257, median_ms=round(med, 3), min_ms=round(lo, 3), max_ms=round(hi, 3)))
        assert len(table.enc) == 257
        # S3d: zstd dictionary, exactly as s3_dict.build_dicts() trains it
        samples = s3_dict._samples(path, rule)
        med, lo, hi = timed(lambda: zstandard.train_dictionary(s3_dict.DICT_SIZE, samples, level=s3_dict.LEVEL))
        d = zstandard.train_dictionary(s3_dict.DICT_SIZE, samples, level=s3_dict.LEVEL)
        rows.append(dict(domain=dom, artifact="S3d zstd dictionary", training_bytes=len(data), samples=len(samples),
                         artifact_bytes=len(d.as_bytes()), median_ms=round(med, 3), min_ms=round(lo, 3), max_ms=round(hi, 3)))
        print(f"{dom}: Huffman table {rows[-2]['median_ms']:.2f} ms, zstd dictionary {rows[-1]['median_ms']:.1f} ms "
              f"({len(data):,} training bytes, {len(samples)} samples)")
    with open("artifact_cost.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote artifact_cost.csv ({len(rows)} rows); zstandard {zstandard.__version__}, "
          f"libzstd {'.'.join(map(str, zstandard.ZSTD_VERSION))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
