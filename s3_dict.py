"""
s3_dict.py - S3d: zstd with a PRE-SHARED, pre-trained dictionary.

Purpose: make the zstd baseline symmetric with S2. S2 is granted a
pre-shared Huffman table trained on a public, seed-disjoint corpus; a
fair zstd baseline must be granted the same kind of pre-shared state.
zstd's native mechanism for this is a pre-trained dictionary (RFC 8878,
Section 5), which is exactly designed for the small-message regime.

Fairness rules (mirroring S2 exactly):
  * Training data: the SAME training corpora used for the S2 tables
    (s2_train.txt, s2_train_c2.txt, s2_train_c3.txt, s2_train_c4.txt),
    seed-disjoint from every test slice.
  * Pre-shared state size is FIXED and reported: DICT_SIZE bytes
    (default 2048 B, matching S2's ~2 KB expanded decode table).
    A size sweep (256 B .. 4096 B) is produced by eval_dict_size.py.
  * No per-message cost: the dictionary ID is NOT written into the
    frame header (write_dict_id=False); both parties know the
    dictionary, exactly as both parties know the S2 code lengths.
  * Two configurations, as for S2:
      S3d-dom : dictionary trained on the test corpus's own domain
      S3d-en  : the English dictionary applied to every corpus
                (domain-shift test)

Training is deterministic for a fixed zstandard/libzstd version and
fixed sample set (zstd's COVER/FastCover trainer is deterministic given
identical inputs and parameters). The dictionary bytes, their SHA-256
and the dictionary ID are written to s3_dicts/ for provenance.

Samples handed to the trainer: for record-structured corpora (C2 NDJSON,
C4 chat lines) one sample per line; for running text (English, uniform)
fixed 128-byte chunks. The sampling rule is part of the pre-shared
provisioning recipe and is recorded in s3_dicts/PROVENANCE.txt.
"""

import hashlib
import pathlib

import zstandard

DICT_SIZE = 2048          # bytes of pre-shared state; reported in the paper
LEVEL = 3                 # same compression level as S3 (dictionary-less)
CHUNK = 128               # sample size for running-text corpora

TRAIN = {
    # domain : (training file, sampling rule)
    "en": ("s2_train.txt", "chunks"),
    "c2": ("s2_train_c2.txt", "lines"),
    "c3": ("s2_train_c3.txt", "chunks"),
    "c4": ("s2_train_c4.txt", "lines"),
}
# C1 is the English-domain corpus: its domain dictionary IS the English one.
DOMAIN_OF = {"c1": "en", "c2": "c2", "c3": "c3", "c4": "c4"}


def _samples(path: str, rule: str) -> list[bytes]:
    data = pathlib.Path(path).read_bytes()
    if rule == "lines":
        return [ln for ln in data.split(b"\n") if ln]
    return [data[i:i + CHUNK] for i in range(0, len(data), CHUNK) if data[i:i + CHUNK]]


def train_dict(domain: str, size: int = DICT_SIZE) -> zstandard.ZstdCompressionDict:
    path, rule = TRAIN[domain]
    return zstandard.train_dictionary(size, _samples(path, rule), level=LEVEL)


def build_dicts(size: int = DICT_SIZE, outdir: str | None = "s3_dicts") -> dict:
    """Train one dictionary per domain; write bytes + provenance."""
    dicts = {dom: train_dict(dom, size) for dom in TRAIN}
    if outdir:
        od = pathlib.Path(outdir)
        od.mkdir(exist_ok=True)
        lines = [f"zstandard {zstandard.__version__} / libzstd {'.'.join(map(str, zstandard.ZSTD_VERSION))}",
                 f"dict_size={size} level={LEVEL} chunk={CHUNK} write_dict_id=False", ""]
        for dom, d in dicts.items():
            b = d.as_bytes()
            (od / f"dict_{dom}_{size}.zdict").write_bytes(b)
            lines.append(f"{dom}: train={TRAIN[dom][0]} rule={TRAIN[dom][1]} "
                         f"bytes={len(b)} dict_id={d.dict_id()} "
                         f"sha256={hashlib.sha256(b).hexdigest()}")
        (od / f"PROVENANCE_{size}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dicts


class DictCodec:
    """encode/decode pair bound to one pre-shared dictionary."""

    def __init__(self, d: zstandard.ZstdCompressionDict):
        self.state_bytes = len(d.as_bytes())
        self._c = zstandard.ZstdCompressor(level=LEVEL, dict_data=d, write_dict_id=False)
        self._d = zstandard.ZstdDecompressor(dict_data=d)

    def encode(self, text: str) -> bytes:
        return self._c.compress(text.encode("utf-8"))

    def decode(self, blob: bytes) -> str:
        return self._d.decompress(blob).decode("utf-8")


def codecs_for(corpus: str, dicts: dict) -> dict:
    """Return {'S3d-dom': DictCodec, 'S3d-en': DictCodec} for a corpus."""
    return {"S3d-dom": DictCodec(dicts[DOMAIN_OF[corpus]]),
            "S3d-en": DictCodec(dicts["en"])}


if __name__ == "__main__":
    ds = build_dicts()
    for dom, d in ds.items():
        print(f"{dom}: {len(d.as_bytes())} B  id={d.dict_id()}")
    print("written to s3_dicts/")
