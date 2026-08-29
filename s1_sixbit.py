"""
s1_sixbit.py — S1: 6-bit static encoding (Somsuk's Table 1 scheme).

Faithful to the original scheme's core: each of the 54 characters maps to
a fixed 6-bit code (index order per alphabet54.ALPHABET54); codes are
concatenated into a continuous bitstream and packed into bytes (the V2
"no per-block waste" strategy, which transfers directly to our byte-
oriented KEM-DEM setting).

DESIGN DECISION (documented for the paper, Section IV-A):
The original work resolves trailing-padding ambiguity ('A' = 000000 vs
zero padding) with the PRT/PST annotation, which prepends an in-band
"n-" prefix. In our byte-stream setting we instead instantiate one code
point from the table's *reserved* code space (the paper allocates codes
54-63 "for prospective future applications"): code 63 (111111) is used
as an end-of-message (EOM) marker. This makes decoding self-delimiting
at a cost of 6 bits per message, is unambiguous against zero padding,
and stays within the scheme's own extension mechanism.

Preprocessing follows the original spec: uppercase-fold first. Characters
still outside the table are handled per `mode`:
  - "strict": raise OutOfAlphabetError   (harness policy for C2 -> N/A)
  - "drop":   remove them, as in the original Algorithm 3.1 line 2;
              the count is reported so the paper can state the residual
              out-of-table fraction for C1. Round-trip then verifies
              against the canonicalized text, not the raw original.
"""

from alphabet54 import ALPHABET54

_CHAR2CODE = {ch: i for i, ch in enumerate(ALPHABET54)}
_CODE2CHAR = {i: ch for i, ch in enumerate(ALPHABET54)}
EOM = 63  # 111111 — reserved code space per the original paper


class OutOfAlphabetError(ValueError):
    pass


def canonicalize(text: str, mode: str = "strict"):
    """Uppercase-fold; handle residual out-of-table chars per mode.
    Returns (canonical_text, dropped_count)."""
    folded = text.upper()
    if mode == "strict":
        for ch in folded:
            if ch not in _CHAR2CODE:
                raise OutOfAlphabetError(f"char {ch!r} not in 54-char table")
        return folded, 0
    if mode == "drop":
        kept = [ch for ch in folded if ch in _CHAR2CODE]
        return "".join(kept), len(folded) - len(kept)
    raise ValueError(f"unknown mode {mode!r}")


def encode(text: str, mode: str = "strict") -> bytes:
    canon, _ = canonicalize(text, mode)
    # 6-bit indices + EOM, packed MSB-first into a byte stream.
    acc = 0
    nbits = 0
    out = bytearray()
    for sym in [_CHAR2CODE[ch] for ch in canon] + [EOM]:
        acc = (acc << 6) | sym
        nbits += 6
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)  # zero-pad final byte
    return bytes(out)


def decode(data: bytes) -> str:
    chars = []
    acc = 0
    nbits = 0
    for byte in data:
        acc = (acc << 8) | byte
        nbits += 8
        while nbits >= 6:
            nbits -= 6
            sym = (acc >> nbits) & 0x3F
            if sym == EOM:
                return "".join(chars)
            if sym not in _CODE2CHAR:
                raise ValueError(f"invalid 6-bit code {sym} (reserved/unassigned)")
            chars.append(_CODE2CHAR[sym])
    raise ValueError("EOM marker not found — truncated or corrupt stream")
