"""
make_c4_modern.py — Build corpus C4 (modern chat-style English messages).

Purpose (robustness check for the paper): C1 is 19th-century novel prose;
C4 is informal, contemporary messaging English — contractions, digits,
times, lowercase, light punctuation — the register actually carried by
secure-messaging systems. Fully seeded and self-contained (no downloads),
mirroring the C2/C3 generator pattern, so the paper only needs to report
the seed for reproducibility.

Stream = newline-separated short messages assembled from seeded template
grammars over fixed word banks. The SAME rng stream is split disjointly:
the first TRAIN_CHARS characters become the pre-shared training text
(s2_train_c4.txt) and the remainder supplies the held-out test corpus
(corpus/c4/c4_{500,1000,5000,20000}.txt) — same-domain but disjoint,
exactly the pre-shared-table deployment model of Section IV-B.

Output: s2_train_c4.txt, corpus_c4/c4_{120,500,1000,2000,3000,5000,20000}.txt
(kept OUTSIDE corpus/ on purpose: harness.py auto-iterates every corpus/*
subdirectory and has no c4 scheme mapping, so placing c4 there would crash
future harness runs)
"""

import argparse
import pathlib
import random

LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
SEED = 20260830          # fixed; report in paper
TRAIN_CHARS = 30000

GREET = ["hey", "hi", "yo", "morning", "hey hey", "ok so", "quick one",
         "btw", "update", "heads up"]
NAME = ["sam", "alex", "mia", "jo", "pat", "kim", "lee", "max", "nat", "ray"]
VERB = ["can you send", "did you see", "i just pushed", "let's grab",
        "i'm running late for", "don't forget", "we moved", "i booked",
        "they cancelled", "she confirmed", "he's asking about",
        "we still need", "i'll finish", "can we reschedule"]
OBJ = ["the report", "lunch", "the 3pm call", "that doc", "the tickets",
       "the demo", "the invoice", "coffee", "the meeting", "the draft",
       "the slides", "the review", "dinner", "the standup", "the release"]
TIME = ["today", "tomorrow", "tonight", "at 9:30", "by 5pm", "on friday",
        "next week", "in 10 mins", "after lunch", "before noon",
        "this weekend", "at 14:00"]
TAIL = ["thanks!", "no rush", "lmk", "sounds good", "makes sense", "sorry!",
        "my bad", "np", "see you there", "call me if not", "ok?", "please",
        "when you can", "asap", ":)"]
REACT = ["ok", "got it", "sure", "yep", "nope", "on it", "done", "perfect",
         "wait what", "really?", "same", "agreed", "one sec", "brb", "omw"]


def one_message(rng: random.Random) -> str:
    r = rng.random()
    if r < 0.15:
        return rng.choice(REACT)
    if r < 0.30:
        return f"{rng.choice(GREET)} {rng.choice(NAME)}, {rng.choice(VERB)} {rng.choice(OBJ)} {rng.choice(TIME)}"
    if r < 0.55:
        return f"{rng.choice(VERB)} {rng.choice(OBJ)} {rng.choice(TIME)} {rng.choice(TAIL)}"
    if r < 0.75:
        return f"{rng.choice(GREET)}, {rng.choice(VERB)} {rng.choice(OBJ)}? {rng.choice(TAIL)}"
    if r < 0.90:
        n = rng.randint(2, 48)
        return f"{rng.choice(NAME)}: {rng.choice(VERB)} {rng.choice(OBJ)} in {n} mins {rng.choice(TAIL)}"
    return f"{rng.choice(REACT)}, {rng.choice(VERB)} {rng.choice(OBJ)} {rng.choice(TIME)}"


def gen_stream(rng: random.Random, min_chars: int) -> str:
    parts = []
    total = 0
    while total < min_chars:
        m = one_message(rng)
        parts.append(m)
        total += len(m) + 1
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="corpus_c4")
    args = ap.parse_args()

    stale = pathlib.Path("corpus/c4")
    if stale.exists():
        print("ERROR: found corpus/c4/ from an earlier version of this script.")
        print("That location breaks future harness.py runs (harness auto-iterates")
        print("every corpus/* subdirectory and has no c4 mapping). Remove it first:")
        print("    rm -rf corpus/c4")
        print("then re-run this script (new location: corpus_c4/).")
        return 3

    rng = random.Random(SEED)
    stream = gen_stream(rng, TRAIN_CHARS + max(LENGTHS) + 3000)

    train = stream[:TRAIN_CHARS]
    test = stream[TRAIN_CHARS:]

    pathlib.Path("s2_train_c4.txt").write_text(train, encoding="utf-8")
    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for n in LENGTHS:
        (outdir / f"c4_{n}.txt").write_text(test[:n], encoding="utf-8")
    print(f"seed={SEED}  train={len(train)} chars  test pool={len(test)} chars")
    for n in LENGTHS:
        print(f"  wrote {outdir}/c4_{n}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
