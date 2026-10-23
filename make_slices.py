"""
make_slices.py - Independent input slices for the timing measurements.

The payload tables use one slice per (corpus, length): corpus/<c>/<c>_<n>.txt.
For timing, a single input per cell measures only run-to-run noise on that
input. This script cuts K = 5 disjoint slices per (corpus, length) from the
same sources and rules, so that latency (and payload) can be summarized
across inputs as well as across repetitions:

  C1  cleaned Gutenberg #1342 text; slice k starts at offset 5000 + k*20000
  C2  the seeded NDJSON stream (seed 20260829); slice k starts at the first
      record boundary at or after k*20300
  C3  the seeded uniform stream (seed 54540229); slice k starts at k*20000

Slice 0 is byte-identical to the corresponding corpus/ file (checked).
Slices of different k never overlap, for every length.

Output: corpus_slices/<c>/<c>_<n>_s<k>.txt   (k = 0..4)
Requires pg1342.txt next to this script (written by make_c1_english.py).
"""

import pathlib
import random
import sys

import make_c1_english as c1
import make_c2_iot as c2
import make_c3_uniform as c3

K = 5
LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
MAXN = max(LENGTHS)


def starts_c1():
    return [c1.START_OFFSET + k * MAXN for k in range(K)]


def main() -> int:
    book = pathlib.Path(f"pg{c1.BOOK_ID}.txt")
    if not book.exists():
        print(f"ERROR: {book} missing (run make_c1_english.py first)", file=sys.stderr)
        return 1
    text1 = c1.clean_gutenberg(book.read_text(encoding="utf-8", errors="replace"))

    rng = random.Random(c2.SEED)
    stride2 = MAXN + 300
    text2 = c2.gen_stream(rng, K * stride2 + MAXN + 1000)
    starts2 = [0]
    for k in range(1, K):
        j = text2.index("\n", k * stride2)
        starts2.append(j + 1)

    rng = random.Random(c3.SEED)
    text3 = "".join(rng.choice(c3.ALPHABET54) for _ in range(K * MAXN))
    starts3 = [k * MAXN for k in range(K)]

    sources = {"c1": (text1, starts_c1()), "c2": (text2, starts2), "c3": (text3, starts3)}
    out = pathlib.Path("corpus_slices")
    for c, (text, starts) in sources.items():
        for a, b in zip(starts, starts[1:]):
            assert b >= a + MAXN, f"{c}: overlapping slices"
        d = out / c
        d.mkdir(parents=True, exist_ok=True)
        for n in LENGTHS:
            for k, s in enumerate(starts):
                sl = text[s:s + n]
                assert len(sl) == n, (c, n, k)
                if k == 0:
                    ref = (pathlib.Path("corpus") / c / f"{c}_{n}.txt").read_text(encoding="utf-8")
                    assert sl == ref, f"{c}_{n}: slice 0 differs from the corpus file"
                (d / f"{c}_{n}_s{k}.txt").write_text(sl, encoding="utf-8")
        (d / "PROVENANCE.txt").write_text(
            f"corpus: {c}\nslices: {K}\nstart offsets: {starts}\n", encoding="utf-8")
    print(f"slices written to {out}/ ({K} per corpus and length)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
