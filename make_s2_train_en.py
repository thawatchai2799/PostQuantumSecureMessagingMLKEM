"""
make_s2_train_en.py - Build the pre-shared ENGLISH training text
(s2_train.txt) used by S2-en / S2-dom(C1) and by the S3d English
dictionary.

Why this script exists: the original s2_train.txt was a single 452-
character paragraph repeated 60 times. For a byte-frequency Huffman
table that is a crude but workable proxy; for a zstd dictionary it is
useless (the dictionary would contain one paragraph). Both pre-shared
artifacts must be trained on genuine English prose that is disjoint
from every C1 test slice.

Source: Project Gutenberg #161, Sense and Sensibility (Jane Austen) -
same author and register as C1 (#1342, Pride and Prejudice) but a
DIFFERENT book, so no sentence, name list or passage of the test corpus
can leak into the pre-shared state. Cleaning and slicing follow
make_c1_english.py exactly (strip PG markers, collapse whitespace,
fixed offset), so the recipe is fully determined by (book id, offset,
length).

Usage:
    python3 make_s2_train_en.py                 # downloads from gutenberg.org
    python3 make_s2_train_en.py --input pg161.txt
Output: s2_train.txt (TRAIN_CHARS characters), s2_train_PROVENANCE.txt
"""

import argparse
import hashlib
import pathlib
import sys
import urllib.request

from make_c1_english import clean_gutenberg

BOOK_ID = 161
URL = f"https://www.gutenberg.org/cache/epub/{BOOK_ID}/pg{BOOK_ID}.txt"
TRAIN_CHARS = 30000      # same size as the C2/C3/C4 training splits
START_OFFSET = 5000      # skip front matter, as for C1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local Gutenberg #161 txt file (skip download)")
    ap.add_argument("--out", default="s2_train.txt")
    ap.add_argument("--offset", type=int, default=START_OFFSET)
    args = ap.parse_args()

    if args.input:
        raw = pathlib.Path(args.input).read_text(encoding="utf-8", errors="replace")
        source = args.input
    else:
        print(f"Downloading Gutenberg #{BOOK_ID} ...")
        with urllib.request.urlopen(URL, timeout=60) as r:
            raw = r.read().decode("utf-8", errors="replace")
        source = URL

    text = clean_gutenberg(raw)
    if len(text) < args.offset + TRAIN_CHARS:
        print(f"ERROR: cleaned text has {len(text)} chars", file=sys.stderr)
        return 1
    train = text[args.offset: args.offset + TRAIN_CHARS]
    pathlib.Path(args.out).write_text(train, encoding="utf-8")
    sha = hashlib.sha256(train.encode("utf-8")).hexdigest()
    pathlib.Path("s2_train_PROVENANCE.txt").write_text(
        f"file: {args.out}\nsource: {source}\nbook_id: {BOOK_ID}\n"
        f"offset: {args.offset}\nchars: {TRAIN_CHARS}\n"
        f"cleaning: strip PG markers, collapse whitespace (make_c1_english.clean_gutenberg)\n"
        f"sha256: {sha}\n", encoding="utf-8")
    print(f"wrote {args.out}: {TRAIN_CHARS} chars from Gutenberg #{BOOK_ID} @ offset {args.offset}; sha256 {sha[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
