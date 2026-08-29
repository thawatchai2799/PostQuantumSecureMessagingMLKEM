"""
test_roundtrip.py — Phase 2 milestone test: all four encoders round-trip
against the real corpus files, plus a bits/char pilot preview.

S3 (zstd, dictionary-less stream) and S4 (UTF-8 passthrough) are thin
enough to live here; S1/S2 are in their own modules.

Round-trip criteria:
  S2/S3/S4: decode(encode(text)) == text  (exact)
  S1:       decode(encode(text)) == canonicalized(text)
            (uppercase-fold + drop, per the original spec; the dropped
             fraction is reported — this is the C1 residual figure for
             the paper. C2 is expected N/A under strict policy (c).)
"""

import pathlib
import sys

import zstandard

import s1_sixbit
from s2_huffman import HuffmanTable

# ---------- S3 / S4 ----------
_zc = zstandard.ZstdCompressor(level=3)          # library default level
_zd = zstandard.ZstdDecompressor()


def s3_encode(text: str) -> bytes:
    return _zc.compress(text.encode("utf-8"))


def s3_decode(data: bytes) -> str:
    return _zd.decompress(data).decode("utf-8")


def s4_encode(text: str) -> bytes:
    return text.encode("utf-8")


def s4_decode(data: bytes) -> str:
    return data.decode("utf-8")


# ---------- test driver ----------
def main() -> int:
    root = pathlib.Path("corpus")

    # S2 training text: disjoint from every test slice. Here we use a
    # placeholder English passage; the real run trains on a separate
    # Gutenberg slice (see make_c1_english.py --offset for a disjoint cut).
    train_path = pathlib.Path("s2_train.txt")
    if not train_path.exists():
        print("ERROR: s2_train.txt missing (S2 training corpus)", file=sys.stderr)
        return 1
    table = HuffmanTable.train(train_path.read_bytes())

    # S2-dom: domain-matched pre-shared tables (per study design). English table
    # for c1; JSON-trained for c2; uniform-trained for c3. Training files
    # are seed-disjoint from every test slice.
    import pathlib as _pl
    dom_tables = {
        "c1": table,
        "c2": HuffmanTable.train(_pl.Path("s2_train_c2.txt").read_bytes()),
        "c3": HuffmanTable.train(_pl.Path("s2_train_c3.txt").read_bytes()),
    }

    schemes = {
        "S1-6bit": (None, s1_sixbit.decode),  # encode is dispatched per-corpus in the loop
        "S2-en":  (table.encode, table.decode),
        "S2-dom": (None, None),  # dispatched per-corpus in the loop
        "S3-zstd": (s3_encode, s3_decode),
        "S4-utf8": (s4_encode, s4_decode),
    }

    # Policy (c): S1 mode per corpus — C1 uses the original spec's fold+drop
    # (residual reported); C2/C3 are strict, so C2 rows surface as N/A.
    s1_mode = {"c1": "drop", "c2": "strict", "c3": "strict"}

    failures = 0
    print(f"{'corpus':<14}{'scheme':<10}{'in':>7}{'out(B)':>8}{'bits/char':>11}  status")
    for cdir in sorted(root.iterdir()):
        if not cdir.is_dir():
            continue
        mode = s1_mode.get(cdir.name, "strict")
        for f in sorted(cdir.glob("c?_*.txt")):
            text = f.read_text(encoding="utf-8")
            for name, (enc, dec) in schemes.items():
                try:
                    if name == "S1-6bit":
                        blob = s1_sixbit.encode(text, mode=mode)
                        back = dec(blob)
                    elif name == "S2-dom":
                        dt_ = dom_tables[cdir.name]
                        blob = dt_.encode(text)
                        back = dt_.decode(blob)
                    else:
                        blob = enc(text)
                        back = dec(blob)
                    if name == "S1-6bit":
                        ref, dropped = s1_sixbit.canonicalize(text, mode)
                        ok = (back == ref)
                        note = f"ok (dropped {dropped}/{len(text)})" if ok else "MISMATCH"
                    else:
                        ok = (back == text)
                        note = "ok" if ok else "MISMATCH"
                    bpc = 8 * len(blob) / len(text)
                    print(f"{f.name:<14}{name:<10}{len(text):>7}{len(blob):>8}{bpc:>11.3f}  {note}")
                    if not ok:
                        failures += 1
                except s1_sixbit.OutOfAlphabetError:
                    print(f"{f.name:<14}{name:<10}{len(text):>7}{'—':>8}{'—':>11}  N/A (alphabet)")
                except Exception as e:  # noqa: BLE001 — report and count
                    print(f"{f.name:<14}{name:<10}{len(text):>7}{'—':>8}{'—':>11}  ERROR: {e}")
                    failures += 1

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: {failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
