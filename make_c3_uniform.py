"""
make_c3_uniform.py — Build corpus C3 (uniform over the 54-char alphabet).

Every character is drawn i.i.d. uniform from Somsuk's Table 1 set, giving
maximum entropy per symbol (log2(54) ~ 5.755 bits). This is the regime
where frequency-based coding (S2 Huffman, S3 zstd) should lose its edge
and fixed-length S1 should be near-optimal — the adversarial counterpart
to C1, preserving the useful design of the original paper's Table 5.

Fully seeded for reproducibility.

Output: corpus/c3/c3_{120,500,1000,2000,3000,5000,20000}.txt
"""

import argparse
import pathlib
import random

from alphabet54 import ALPHABET54

LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
SEED = 54540229  # fixed; report in paper


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="corpus/c3")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    stream = "".join(rng.choice(ALPHABET54) for _ in range(max(LENGTHS)))

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for n in LENGTHS:
        slice_ = stream[:n]
        assert len(slice_) == n
        p = outdir / f"c3_{n}.txt"
        p.write_text(slice_, encoding="utf-8")
        print(f"  wrote {p}  ({n} chars)")

    (outdir / "PROVENANCE.txt").write_text(
        f"generator: make_c3_uniform.py\nseed: {args.seed}\n"
        "distribution: i.i.d. uniform over 54-char Table 1 alphabet\n",
        encoding="utf-8",
    )
    print("C3 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
