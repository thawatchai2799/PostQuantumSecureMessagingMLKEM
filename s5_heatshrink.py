"""
s5_heatshrink.py - S5: heatshrink, an LZSS compressor designed for
microcontrollers (fixed, tiny working memory; no heap; streaming API).

Why it is in the comparison: S3/S3d use libzstd, whose decoder working set
is far above what a constrained node can afford, so a reviewer may ask how
a compressor that *does* fit such a node performs on short messages.
heatshrink is the common answer in the embedded world (it is used in
several RTOS and bootloader projects) and has a Python binding
(heatshrink2) that wraps the reference C implementation.

Two configurations are measured; both are stateless (no pre-shared state):

    S5-hs8   window 2^8  = 256 B, lookahead 2^4  (typical MCU setting;
             the decoder needs ~256 B of RAM)
    S5-hs11  window 2^11 = 2 KiB, lookahead 2^4  (library default; working
             memory comparable to the 2 KiB zstd dictionary of S3d)

Round trip is exact (lossless byte stream, UTF-8 text in and out).
"""

import heatshrink2

CONFIGS = {
    "S5-hs8":  (8, 4),
    "S5-hs11": (11, 4),
}


class HeatshrinkCodec:
    def __init__(self, window_sz2: int, lookahead_sz2: int):
        self.w = window_sz2
        self.l = lookahead_sz2

    def encode(self, text: str) -> bytes:
        return heatshrink2.encode(text.encode("utf-8"),
                                  window_sz2=self.w, lookahead_sz2=self.l)

    def decode(self, blob: bytes) -> str:
        return heatshrink2.decode(blob, window_sz2=self.w,
                                  lookahead_sz2=self.l).decode("utf-8")


def codecs() -> dict:
    return {name: HeatshrinkCodec(w, l) for name, (w, l) in CONFIGS.items()}


def version() -> str:
    return getattr(heatshrink2, "__version__", "?")


if __name__ == "__main__":
    import sys
    print(f"heatshrink2 {version()}")
    for name, c in codecs().items():
        s = "The quick brown fox jumps over the lazy dog. " * 4
        e = c.encode(s)
        assert c.decode(e) == s
        print(f"{name}: {len(s)} chars -> {len(e)} bytes ({8*len(e)/len(s):.3f} bits/char)")
    sys.exit(0)
