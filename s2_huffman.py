"""
s2_huffman.py - S2: pre-shared canonical Huffman coding.

The table is built ONCE from a training corpus (disjoint from all test
slices) and is assumed pre-shared by both parties - exactly the same
assumption the 6-bit static table (S1) enjoys. Nothing is transmitted
per message. This removes the tree-transmission cost that the original
paper charged against Huffman coding.

Construction (deterministic - both sides derive an identical table):
  1. Symbol set = all 256 byte values + EOF (symbol 256).
  2. Frequencies = counts in training bytes, add-one smoothing on every
     byte value (so unseen-but-possible bytes still get a code), EOF = 1.
  3. Huffman code lengths via the standard heap algorithm
     (ties broken deterministically by symbol id).
  4. Canonical code assignment: sort by (length, symbol), assign
     sequentially - the table is then fully described by the 257 code
     lengths alone (what would be pre-shared in a real deployment).

Encode: text -> UTF-8 bytes -> codes + EOF, zero-padded to a byte.
Decode: walk bits against the canonical table until EOF. Padding cannot
be misread: EOF terminates before padding is reached, and the decoder
stops there.
"""

import heapq
from collections import Counter

EOF = 256


class HuffmanTable:
    def __init__(self, code_lengths):
        """code_lengths: dict symbol -> length (canonical description)."""
        self.lengths = dict(code_lengths)
        # Canonical assignment: sort by (length, symbol).
        items = sorted(self.lengths.items(), key=lambda kv: (kv[1], kv[0]))
        self.enc = {}          # symbol -> (code_int, length)
        self.dec = {}          # (length, code_int) -> symbol
        code = 0
        prev_len = items[0][1]
        for sym, ln in items:
            code <<= (ln - prev_len)
            prev_len = ln
            self.enc[sym] = (code, ln)
            self.dec[(ln, code)] = sym
            code += 1
        self.max_len = items[-1][1]

    @classmethod
    def train(cls, training_bytes: bytes) -> "HuffmanTable":
        freq = Counter({b: 1 for b in range(256)})   # add-one smoothing
        freq.update(training_bytes)
        freq[EOF] = 1
        # Heap of (weight, tiebreak, node); node = symbol or (left,right).
        heap = [(w, s, s) for s, w in freq.items()]
        heapq.heapify(heap)
        tick = len(heap)
        while len(heap) > 1:
            w1, _, n1 = heapq.heappop(heap)
            w2, _, n2 = heapq.heappop(heap)
            heapq.heappush(heap, (w1 + w2, tick, (n1, n2)))
            tick += 1
        lengths = {}

        def walk(node, depth):
            if isinstance(node, tuple):
                walk(node[0], depth + 1)
                walk(node[1], depth + 1)
            else:
                lengths[node] = max(depth, 1)

        walk(heap[0][2], 0)
        return cls(lengths)

    def encode(self, text: str) -> bytes:
        return self.encode_bytes(text.encode("utf-8"))

    def encode_bytes(self, data: bytes) -> bytes:
        """Encode arbitrary bytes (used for binary payloads such as CBOR)."""
        acc = 0
        nbits = 0
        out = bytearray()
        for sym in list(data) + [EOF]:
            code, ln = self.enc[sym]
            acc = (acc << ln) | code
            nbits += ln
            while nbits >= 8:
                nbits -= 8
                out.append((acc >> nbits) & 0xFF)
        if nbits:
            out.append((acc << (8 - nbits)) & 0xFF)
        return bytes(out)

    def decode(self, data: bytes) -> str:
        return self.decode_bytes(data).decode("utf-8")

    def decode_bytes(self, data: bytes) -> bytes:
        out = bytearray()
        code = 0
        ln = 0
        for byte in data:
            for shift in range(7, -1, -1):
                code = (code << 1) | ((byte >> shift) & 1)
                ln += 1
                sym = self.dec.get((ln, code))
                if sym is not None:
                    if sym == EOF:
                        return bytes(out)
                    out.append(sym)
                    code = 0
                    ln = 0
                if ln > self.max_len:
                    raise ValueError("invalid bitstream (no code matched)")
        raise ValueError("EOF symbol not found - truncated or corrupt stream")
