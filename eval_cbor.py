"""
eval_cbor.py - Binary serialization (CBOR, RFC 8949) as a telemetry baseline,
alone and composed with the byte-level encoders.

The encoders in this study transform whatever bytes an application emits.
A schema-based binary serialization changes what the application emits.
This script measures both, and their composition, on the two telemetry
corpora: C2 (synthetic NDJSON records) and C5 (real Intel Lab readings).

Message unit: k complete records (C2: k = 1, 4, 16 ~ 120, 480, 1,900 chars;
C5: k = 2, 8, 32 ~ 125, 500, 2,000 chars). For each k, the first M = 20
disjoint messages of the test stream are encoded and the mean size per
record is reported, so that a single message boundary does not decide it.

Formats (all lossless at the level of the record's values):
  text        the records as text lines, as in the corpus (the S4 baseline)
  cbor-map    each record a CBOR map {field name: value} (self-describing)
  cbor-array  each record a CBOR array of values; field names and order are
              part of the schema both ends know (the Protobuf-like case)
  cbor-packed as cbor-array, encoded the way a schema author would:
              timestamps as integers (C2: Unix seconds; C5: Unix
              microseconds), device ids as integers, and measurements as
              fixed-point integers at the precision present in the data
              (C2: the generator's rounding; C5: the most decimals of each
              field over the training and test lines)
CBOR is encoded canonically (cbor2, canonical=True): integers and floats in
their shortest exact form; numbers not exactly representable in binary32
stay binary64 (9 bytes), which is why cbor-array can exceed the text form.
Every decoded message is checked value by value against its source records.

Encoders applied on top (artifacts trained on the same held-out training
records, converted to the same format):
  S2-dom   canonical Huffman table trained on the training bytes
  S3-zstd  zstd level 3, no dictionary
  S3d-dom  zstd level 3 with a 2,048 B dictionary trained on the records
  S5-hs8   heatshrink, 2^8 window

Output: results_cbor.csv
Usage:  python3 eval_cbor.py      (needs data.txt.gz and the training texts)
"""

import calendar
import csv
import datetime as dt
import io
import json
import pathlib
import random
import sys
from decimal import Decimal

import cbor2
import heatshrink2
import zstandard

import make_c2_iot
import make_c5_intel
import s3_dict
from s2_huffman import HuffmanTable

MESSAGES = 20
GROUPS = {"c2": [1, 4, 16], "c5": [2, 8, 32]}
FORMATS = ("text", "cbor-map", "cbor-array", "cbor-packed")
C5_FIELDS = ["date", "time", "epoch", "moteid", "temperature", "humidity", "light", "voltage"]
C5_NUM = ["temperature", "humidity", "light", "voltage"]
C2_SCALE = {"temp_c": 100, "rh_pct": 10, "pm25": 10, "batt_v": 100}
SCALES = {}


# ---------------- records ----------------
def c2_records(text: str):
    return [json.loads(ln) for ln in text.split("\n") if ln.strip()]


def c5_records(text: str):
    """C5 numeric fields are kept as their text so that values stay exact."""
    out = []
    for ln in text.split("\n"):
        f = ln.split()
        if len(f) == 8:
            out.append(dict(zip(C5_FIELDS, [f[0], f[1], int(f[2]), int(f[3])] + f[4:])))
    return out


def fields_of(corpus, rec):
    return list(rec.keys()) if corpus == "c2" else C5_FIELDS


def value(corpus, rec, k):
    return float(rec[k]) if corpus == "c5" and k in C5_NUM else rec[k]


def decimals(s: str) -> int:
    return len(s.split(".")[1]) if "." in s else 0


def pack(corpus, r):
    if corpus == "c2":
        ts = calendar.timegm(dt.datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
        out = [int(r["dev"][3:]), ts, r["seq"]]
        for k in ("temp_c", "rh_pct", "pm25", "batt_v"):
            v = round(r[k] * C2_SCALE[k])
            assert Decimal(v) / C2_SCALE[k] == Decimal(repr(r[k])), (k, r[k])
            out.append(v)
        return out + [r["rssi"]]
    sec, _, frac = r["time"].partition(".")
    base = calendar.timegm(dt.datetime.strptime(r["date"] + " " + sec, "%Y-%m-%d %H:%M:%S").timetuple())
    out = [base * 1_000_000 + int((frac + "000000")[:6]), r["epoch"], r["moteid"]]
    for k in C5_NUM:
        v = Decimal(r[k]) * (10 ** SCALES["c5"][k])
        assert v == v.to_integral_value(), (k, r[k])
        out.append(int(v))
    return out


def unpacked_matches(corpus, packed, r):
    if corpus == "c2":
        return packed == pack("c2", r)
    if packed[1:3] != [r["epoch"], r["moteid"]]:
        return False
    sec, _, frac = r["time"].partition(".")
    base = calendar.timegm(dt.datetime.strptime(r["date"] + " " + sec, "%Y-%m-%d %H:%M:%S").timetuple())
    if packed[0] != base * 1_000_000 + int((frac + "000000")[:6]):
        return False
    return all(Decimal(packed[3 + i]) / (10 ** SCALES["c5"][k]) == Decimal(r[k]) for i, k in enumerate(C5_NUM))


# ---------------- formats ----------------
def serialize(fmt, corpus, recs) -> bytes:
    if fmt == "text":
        if corpus == "c2":
            return "\n".join(json.dumps(r, separators=(",", ":")) for r in recs).encode()
        return "\n".join(" ".join(str(r[k]) for k in C5_FIELDS) for r in recs).encode()
    if fmt == "cbor-map":
        objs = [{k: value(corpus, r, k) for k in fields_of(corpus, r)} for r in recs]
    elif fmt == "cbor-array":
        objs = [[value(corpus, r, k) for k in fields_of(corpus, r)] for r in recs]
    else:
        objs = [pack(corpus, r) for r in recs]
    return b"".join(cbor2.dumps(o, canonical=True) for o in objs)


def deserialized_matches(fmt, corpus, blob, recs) -> bool:
    if fmt == "text":
        return blob == serialize("text", corpus, recs)
    f = io.BytesIO(blob)
    dec = cbor2.CBORDecoder(f)
    items = []
    while f.tell() < len(blob):
        items.append(dec.decode())
    if len(items) != len(recs):
        return False
    for it, r in zip(items, recs):
        if fmt == "cbor-map" and it != {k: value(corpus, r, k) for k in fields_of(corpus, r)}:
            return False
        if fmt == "cbor-array" and it != [value(corpus, r, k) for k in fields_of(corpus, r)]:
            return False
        if fmt == "cbor-packed" and not unpacked_matches(corpus, it, r):
            return False
    return True


# ---------------- data ----------------
def test_stream(corpus):
    if corpus == "c2":
        return c2_records(make_c2_iot.gen_stream(random.Random(make_c2_iot.SEED), 200_000))
    lines = []
    for ln in make_c5_intel.kept_lines(pathlib.Path("data.txt.gz")):
        lines.append(ln)
        if len(lines) >= max(GROUPS["c5"]) * MESSAGES:
            break
    return c5_records("\n".join(lines))


def training(corpus):
    text = pathlib.Path(f"s2_train_{corpus}.txt").read_text(encoding="utf-8")
    return c2_records(text) if corpus == "c2" else c5_records(text)


# ---------------- main ----------------
def main() -> int:
    for req in ["s2_train_c2.txt", "s2_train_c5.txt", "data.txt.gz"]:
        if not pathlib.Path(req).exists():
            raise SystemExit(f"ERROR: missing {req} (run run_all.py, which builds it)")
    rows, bad = [], 0
    zc, zd = zstandard.ZstdCompressor(level=3), zstandard.ZstdDecompressor()
    for corpus, ks in GROUPS.items():
        recs, train = test_stream(corpus), training(corpus)
        if corpus == "c5":
            SCALES["c5"] = {k: max(decimals(r[k]) for r in recs + train) for k in C5_NUM}
            print("C5 fixed-point decimals per field:", SCALES["c5"])
        art = {}
        for fmt in FORMATS:
            samples = [serialize(fmt, corpus, [r]) for r in train]
            art[fmt] = (HuffmanTable.train(b"".join(samples)),
                        zstandard.train_dictionary(s3_dict.DICT_SIZE, samples, level=s3_dict.LEVEL))
        for k in ks:
            msgs = [recs[i * k:(i + 1) * k] for i in range(MESSAGES)]
            assert all(len(m) == k for m in msgs), (corpus, k)
            chars = sum(len(serialize("text", corpus, m)) for m in msgs) / MESSAGES
            for fmt in FORMATS:
                table, d = art[fmt]
                dc = zstandard.ZstdCompressor(level=3, dict_data=d, write_dict_id=False)
                dd = zstandard.ZstdDecompressor(dict_data=d)
                coders = {
                    "none": (lambda b: b, lambda b: b),
                    "S2-dom": (table.encode_bytes, table.decode_bytes),
                    "S3-zstd": (zc.compress, zd.decompress),
                    "S3d-dom": (dc.compress, dd.decompress),
                    "S5-hs8": (lambda b: heatshrink2.encode(b, window_sz2=8, lookahead_sz2=4),
                               lambda b: heatshrink2.decode(b, window_sz2=8, lookahead_sz2=4)),
                }
                for name, (enc, dec) in coders.items():
                    total, ok_all = 0, True
                    for m in msgs:
                        raw = serialize(fmt, corpus, m)
                        blob = enc(raw)
                        back = dec(blob)
                        ok = back == raw and deserialized_matches(fmt, corpus, back, m)
                        ok_all &= ok
                        total += len(blob)
                    bad += not ok_all
                    rows.append({"corpus": corpus, "records_per_msg": k, "messages": MESSAGES,
                                 "mean_text_chars": round(chars, 1), "format": fmt, "encoder": name,
                                 "mean_bytes_per_msg": round(total / MESSAGES, 2),
                                 "mean_bytes_per_record": round(total / MESSAGES / k, 2),
                                 "roundtrip": "ok" if ok_all else "MISMATCH"})
                    print(f"{corpus} k={k:3d} {fmt:11s} {name:8s} {total / MESSAGES:9.2f} B/msg "
                          f"{total / MESSAGES / k:8.2f} B/record")
    with open("results_cbor.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote results_cbor.csv ({len(rows)} rows)  mismatches={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
