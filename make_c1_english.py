"""
make_c1_english.py - Build corpus C1 (English text) from Project Gutenberg.

Reproducibility: the paper reports the Gutenberg book ID, the strip
markers, and the slice offsets - anyone can rebuild byte-identical files.

Default book: #1342 Pride and Prejudice (public domain, plain-text UTF-8).

Usage:
    python3 make_c1_english.py                # downloads from gutenberg.org
    python3 make_c1_english.py --input pg1342.txt   # use a local copy

Output: corpus/c1/c1_{120,500,1000,2000,3000,5000,20000}.txt
Each file is exactly N characters, sliced from cleaned text starting at
a fixed offset (default 5000) to skip front matter like tables of contents.
"""

import argparse
import pathlib
import re
import sys
import urllib.request

BOOK_ID = 1342
URL = f"https://www.gutenberg.org/cache/epub/{BOOK_ID}/pg{BOOK_ID}.txt"
LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
START_OFFSET = 5000  # chars into cleaned text; fixed for reproducibility

START_RE = re.compile(r"\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.S)
END_RE = re.compile(r"\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG.*", re.S)


def clean_gutenberg(raw: str) -> str:
    """Strip PG header/footer, collapse whitespace to single spaces."""
    m = START_RE.search(raw)
    if m:
        raw = raw[m.end():]
    m = END_RE.search(raw)
    if m:
        raw = raw[: m.start()]
    # Normalize all whitespace (newlines, tabs, multiple spaces) to one space.
    return re.sub(r"\s+", " ", raw).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="local Gutenberg txt file (skip download)")
    ap.add_argument("--outdir", default="corpus/c1")
    ap.add_argument("--offset", type=int, default=START_OFFSET)
    args = ap.parse_args()

    if args.input:
        raw = pathlib.Path(args.input).read_text(encoding="utf-8", errors="replace")
        source = args.input
    else:
        print(f"Downloading Gutenberg #{BOOK_ID} ...")
        with urllib.request.urlopen(URL, timeout=60) as r:
            data = r.read()
        raw = data.decode("utf-8", errors="replace")
        source = URL
        # keep the downloaded book: make_slices.py cuts further slices from it
        pathlib.Path(f"pg{BOOK_ID}.txt").write_bytes(data)

    text = clean_gutenberg(raw)
    need = args.offset + max(LENGTHS)
    if len(text) < need:
        print(f"ERROR: cleaned text has {len(text)} chars, need {need}", file=sys.stderr)
        return 1

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for n in LENGTHS:
        slice_ = text[args.offset : args.offset + n]
        assert len(slice_) == n
        p = outdir / f"c1_{n}.txt"
        p.write_text(slice_, encoding="utf-8")
        print(f"  wrote {p}  ({n} chars)")

    (outdir / "PROVENANCE.txt").write_text(
        f"source: {source}\nbook_id: {BOOK_ID}\noffset: {args.offset}\n"
        f"cleaning: strip PG markers, collapse whitespace\n",
        encoding="utf-8",
    )
    print("C1 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
