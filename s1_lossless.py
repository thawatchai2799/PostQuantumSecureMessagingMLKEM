"""
s1_lossless.py - What the 6-bit static code costs when it is made lossless.

S1 as specified folds case and drops characters outside its 54-character
alphabet, so its payload figures describe a canonicalized text. This script
quantifies that loss and prices the smallest lossless extension of the
scheme that stays within its own code space (codes 54-63 are reserved in
the original table):

  S1-x: codes 0-53 as in S1; code 61 toggles a case lock (letters are
        emitted as their uppercase code and read back in the current case);
        code 62 escapes one raw byte (the 8 bits follow the code), used for
        every UTF-8 byte of a character outside the alphabet; code 63 is
        the end-of-message marker, as in S1.

The encoder is exact: decode(encode(t)) == t for every input, checked here.
Nothing is timed; the payload is a deterministic function of the input.

Per corpus file the script reports the information S1 discards (the share
of characters dropped and the share of letters whose case is folded) and
the bits per character of S1 (drop mode), S1-x and UTF-8.

Usage:  python3 s1_lossless.py            (reads corpus/, corpus_c4/, corpus_c5/)
Output: results_s1_lossless.csv
"""

import csv
import pathlib
import sys

from alphabet54 import ALPHABET54
import s1_sixbit

CODE = {ch: i for i, ch in enumerate(ALPHABET54)}
SHIFT, ESC, EOM = 61, 62, 63
LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
CORPORA = [("c1", "corpus/c1"), ("c2", "corpus/c2"), ("c3", "corpus/c3"),
           ("c4", "corpus_c4"), ("c5", "corpus_c5")]


class BitWriter:
    def __init__(self):
        self.acc, self.n, self.out = 0, 0, bytearray()

    def put(self, value, bits):
        self.acc = (self.acc << bits) | value
        self.n += bits
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 0xFF)

    def finish(self):
        if self.n:
            self.out.append((self.acc << (8 - self.n)) & 0xFF)
        return bytes(self.out)


class BitReader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def get(self, bits):
        v = 0
        for _ in range(bits):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v


def encode(text: str) -> bytes:
    w = BitWriter()
    lower = False
    for ch in text:
        if ch.isalpha() and ch.upper() in CODE and len(ch.upper()) == 1 and ch.lower() != ch.upper():
            want = ch.islower()
            if want != lower:
                w.put(SHIFT, 6)
                lower = want
            w.put(CODE[ch.upper()], 6)
        elif ch in CODE:
            w.put(CODE[ch], 6)
        else:
            for b in ch.encode("utf-8"):
                w.put(ESC, 6)
                w.put(b, 8)
    w.put(EOM, 6)
    return w.finish()


def decode(data: bytes) -> str:
    r = BitReader(data)
    out = bytearray()
    lower = False
    while True:
        c = r.get(6)
        if c == EOM:
            break
        if c == SHIFT:
            lower = not lower
        elif c == ESC:
            out.append(r.get(8))
        else:
            ch = ALPHABET54[c]
            if lower and ch.isalpha():
                ch = ch.lower()
            out += ch.encode("ascii")
    return out.decode("utf-8")


def main() -> int:
    rows = []
    for corpus, d in CORPORA:
        for n in LENGTHS:
            p = pathlib.Path(d) / f"{corpus}_{n}.txt"
            if not p.exists():
                print(f"missing {p}, skipped", file=sys.stderr)
                continue
            text = p.read_text(encoding="utf-8")
            assert len(text) == n, (p, len(text))
            enc = encode(text)
            back = decode(enc)
            if back != text:
                raise SystemExit(f"round trip failed on {p}")
            letters = sum(ch.isalpha() for ch in text)
            folded = sum(ch.isalpha() and ch.islower() for ch in text)
            canon, dropped = s1_sixbit.canonicalize(text, mode="drop")
            s1_bytes = len(s1_sixbit.encode(text, mode="drop"))
            rows.append({"corpus": corpus, "length": n, "utf8_bytes": len(text.encode("utf-8")),
                         "dropped_chars": dropped, "dropped_pct": round(100 * dropped / n, 2),
                         "letters": letters, "case_folded_letters": folded,
                         "case_folded_pct_of_letters": round(100 * folded / letters, 1) if letters else 0.0,
                         "s1_drop_bytes": s1_bytes, "s1_drop_bpc": round(8 * s1_bytes / n, 3),
                         "s1x_bytes": len(enc), "s1x_bpc": round(8 * len(enc) / n, 3),
                         "utf8_bpc": round(8 * len(text.encode("utf-8")) / n, 3)})
            print(f"{corpus} {n:6d}  dropped {dropped:5d} ({rows[-1]['dropped_pct']:5.2f}%)  folded letters "
                  f"{rows[-1]['case_folded_pct_of_letters']:5.1f}%  S1-drop {rows[-1]['s1_drop_bpc']:.3f}  "
                  f"S1-x {rows[-1]['s1x_bpc']:.3f}  UTF-8 {rows[-1]['utf8_bpc']:.3f} bits/char")
    with open("results_s1_lossless.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote results_s1_lossless.csv ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
