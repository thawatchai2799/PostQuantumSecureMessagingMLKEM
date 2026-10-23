"""
s6_unishox.py - S6: Unishox2, a hybrid coder for short Unicode strings.

Unishox2 combines a fixed, built-in model of common characters and
character sequences (set states, frequent n-grams, templates) with
variable-length codes and back-references within the message. Nothing
is pre-shared beyond what is compiled into the library, so it is
stateless across messages, like S3 and S5, but unlike them it is built
for messages of a few dozen to a few hundred characters.

The Python side calls the vendored C library (third_party/unishox2,
tag 1.0.4) through ctypes, so the pipeline and the C timing driver
(bench_lib.c) run the same code. run_all.py builds libunishox2.so:
    gcc -O2 -shared -fPIC -o libunishox2.so third_party/unishox2/unishox2.c
"""

import ctypes
import pathlib

_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        path = pathlib.Path(__file__).resolve().parent / "libunishox2.so"
        if not path.exists():
            raise RuntimeError("libunishox2.so not found; build it with: gcc -O2 -shared -fPIC "
                               "-o libunishox2.so third_party/unishox2/unishox2.c")
        lib = ctypes.CDLL(str(path))
        for fn in (lib.unishox2_compress_simple, lib.unishox2_decompress_simple):
            fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
            fn.restype = ctypes.c_int
        _LIB = lib
    return _LIB


class UnishoxCodec:
    def encode(self, text: str) -> bytes:
        b = text.encode("utf-8")
        out = ctypes.create_string_buffer(2 * len(b) + 64)
        n = _lib().unishox2_compress_simple(b, len(b), out)
        if n < 0:
            raise RuntimeError("unishox2 compression failed")
        return out.raw[:n]

    def decode(self, blob: bytes) -> str:
        # the stream carries no length header: size the buffer generously
        out = ctypes.create_string_buffer(max(1 << 21, 64 * len(blob)))
        n = _lib().unishox2_decompress_simple(blob, len(blob), out)
        if n < 0:
            raise RuntimeError("unishox2 decompression failed")
        return out.raw[:n].decode("utf-8")


if __name__ == "__main__":
    c = UnishoxCodec()
    s = "The quick brown fox jumps over the lazy dog. " * 3
    e = c.encode(s)
    assert c.decode(e) == s
    print(f"S6-unishox: {len(s)} chars -> {len(e)} bytes ({8 * len(e) / len(s):.3f} bits/char)")
