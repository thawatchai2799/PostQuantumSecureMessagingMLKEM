"""
make_c5_intel.py - Build corpus C5 (real sensor readings) from the Intel
Berkeley Research Lab data set (54 Mica2Dot motes, 28 Feb - 5 Apr 2004,
about 2.3 million readings; https://db.csail.mit.edu/labdata/labdata.html).

Each reading is one text line as distributed by the data set:
    date time epoch moteid temperature humidity light voltage
e.g. "2004-03-31 03:38:15.757551 2 1 122.153 -3.91901 11.04 2.03397".
This is the record a gateway logs for every radio message, so C5 is the
real-data counterpart of the synthetic NDJSON corpus C2.

Rules (fixed, reported in the paper):
  - keep lines with exactly 8 whitespace-separated fields, in file order;
    re-join the fields with single spaces; terminate each line with "\n"
  - test stream: from the first kept line; slices of 120 ... 20,000 chars
  - training text (s2_train_c5.txt): whole lines from kept line 1,000,000
    on, until at least 30,000 characters -- disjoint from the test stream
    by about a million readings

Usage:
    python3 make_c5_intel.py --input data.txt.gz     (or data.txt)
Download data.txt.gz (34 MB) from https://db.csail.mit.edu/labdata/data.txt.gz
and place it next to this script.

Output: corpus_c5/c5_{120,...,20000}.txt, s2_train_c5.txt, provenance file.
"""

import argparse
import gzip
import hashlib
import pathlib
import sys

LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
TRAIN_FROM_LINE = 1_000_000
TRAIN_CHARS = 30_000


def kept_lines(path: pathlib.Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="ascii", errors="replace") as f:
        for line in f:
            fields = line.split()
            if len(fields) == 8:
                yield " ".join(fields)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data.txt.gz")
    ap.add_argument("--outdir", default="corpus_c5")
    args = ap.parse_args()
    src = pathlib.Path(args.input)
    if not src.exists():
        print(f"ERROR: {src} not found. Download https://db.csail.mit.edu/labdata/data.txt.gz "
              "and place it next to this script.", file=sys.stderr)
        return 1

    test, train, total = [], [], 0
    test_chars = train_chars = 0
    for i, line in enumerate(kept_lines(src)):
        total = i + 1
        if test_chars < max(LENGTHS) + 200:
            test.append(line)
            test_chars += len(line) + 1
        if i >= TRAIN_FROM_LINE and train_chars < TRAIN_CHARS:
            train.append(line)
            train_chars += len(line) + 1
        if test_chars >= max(LENGTHS) + 200 and train_chars >= TRAIN_CHARS:
            break
    if train_chars < TRAIN_CHARS:
        print("ERROR: input has too few lines for the training split", file=sys.stderr)
        return 1

    stream = "\n".join(test) + "\n"
    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for n in LENGTHS:
        (out / f"c5_{n}.txt").write_text(stream[:n], encoding="utf-8")
    pathlib.Path("s2_train_c5.txt").write_text("\n".join(train) + "\n", encoding="utf-8")

    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    (out / "PROVENANCE.txt").write_text(
        f"source: Intel Berkeley Research Lab data set, file {src.name}\n"
        f"source sha256: {digest}\n"
        "rule: keep lines with 8 fields, file order, single-space join\n"
        f"test: kept lines 0..{len(test) - 1}\n"
        f"training: kept lines {TRAIN_FROM_LINE}..{TRAIN_FROM_LINE + len(train) - 1} "
        f"({train_chars} chars) -> s2_train_c5.txt\n",
        encoding="utf-8")
    print(f"C5: test {len(test)} lines, training {len(train)} lines ({train_chars} chars); "
          f"source sha256 {digest[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
