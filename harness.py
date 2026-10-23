"""
harness.py - Phase 2/3 benchmark harness.

Pipeline per message (the FIPS 203 KEM-DEM construction):
    sender:   ss, ct_kem = ML-KEM-512.Encaps(pk)     [once per session]
              key = HKDF-SHA256(ss)
              payload = AES-256-GCM(key, nonce, encode_S(text))
    receiver: ss = ML-KEM-512.Decaps(sk, ct_kem)
              text' = decode_S(AES-256-GCM^-1(key, nonce, payload))
    verify:   text' == reference (exact for S2*/S3/S4; canonicalized for S1)

Measured per (corpus x length x scheme), REPS repetitions each:
    t_encode, t_aead_enc, t_aead_dec, t_decode   (perf_counter_ns)
    encoded_bytes, aead_ct_bytes                 (sizes)
KEM costs (keygen/encaps/decaps) are session-level and measured once,
REPS times, into their own CSV - they are scheme-independent by design.

Output: results_pipeline.csv, results_kem.csv
Usage:  python3 harness.py [--reps 30] [--warmup 5] [--outdir results]
"""

import argparse
import csv
import hashlib
import hmac
import pathlib
import secrets
import time

import oqs
import zstandard
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import s1_sixbit
import s3_dict
import s5_heatshrink
import s6_unishox
from s2_huffman import HuffmanTable

# ---------------- HKDF-SHA256 (extract+expand, RFC 5869) ----------------
def hkdf_sha256(ikm: bytes, info: bytes = b"kemdem-aes256", length: int = 32) -> bytes:
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    t = b""
    okm = b""
    i = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]


# ---------------- scheme registry ----------------
def build_schemes():
    en = HuffmanTable.train(pathlib.Path("s2_train.txt").read_bytes())
    dom = {
        "c1": en,
        "c2": HuffmanTable.train(pathlib.Path("s2_train_c2.txt").read_bytes()),
        "c3": HuffmanTable.train(pathlib.Path("s2_train_c3.txt").read_bytes()),
    }
    zc = zstandard.ZstdCompressor(level=3)
    zd = zstandard.ZstdDecompressor()
    dicts = s3_dict.build_dicts()          # S3d: pre-shared zstd dictionaries
    hs = s5_heatshrink.codecs()            # S5: MCU-class LZSS, stateless
    ux = s6_unishox.UnishoxCodec()         # S6: short-string coder, stateless

    def s3e(t): return zc.compress(t.encode("utf-8"))
    def s3d(b): return zd.decompress(b).decode("utf-8")
    def s4e(t): return t.encode("utf-8")
    def s4d(b): return b.decode("utf-8")

    s1_mode = {"c1": "drop", "c2": "strict", "c3": "strict"}

    def make(corpus):
        d3 = s3_dict.codecs_for(corpus, dicts)
        return {
            "S1-6bit": (lambda t: s1_sixbit.encode(t, mode=s1_mode[corpus]),
                        s1_sixbit.decode,
                        lambda t: s1_sixbit.canonicalize(t, s1_mode[corpus])[0]),
            "S2-en":  (en.encode, en.decode, lambda t: t),
            "S2-dom": (dom[corpus].encode, dom[corpus].decode, lambda t: t),
            "S3-zstd": (s3e, s3d, lambda t: t),
            "S3d-dom": (d3["S3d-dom"].encode, d3["S3d-dom"].decode, lambda t: t),
            "S3d-en":  (d3["S3d-en"].encode, d3["S3d-en"].decode, lambda t: t),
            "S4-utf8": (s4e, s4d, lambda t: t),
            "S5-hs8":  (hs["S5-hs8"].encode, hs["S5-hs8"].decode, lambda t: t),
            "S5-hs11": (hs["S5-hs11"].encode, hs["S5-hs11"].decode, lambda t: t),
            "S6-unishox": (ux.encode, ux.decode, lambda t: t),
        }
    return make


# ---------------- benchmark ----------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--outdir", default="results")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(exist_ok=True)

    # --- KEM session costs (scheme-independent), REPS samples ---
    kem_rows = []
    for _ in range(args.warmup):
        k = oqs.KeyEncapsulation("ML-KEM-512")
        pk = k.generate_keypair(); ct, ss = k.encap_secret(pk); k.decap_secret(ct)
    for rep in range(args.reps):
        k = oqs.KeyEncapsulation("ML-KEM-512")
        t0 = time.perf_counter_ns(); pk = k.generate_keypair(); t1 = time.perf_counter_ns()
        ct, ss = k.encap_secret(pk); t2 = time.perf_counter_ns()
        ss2 = k.decap_secret(ct); t3 = time.perf_counter_ns()
        assert ss == ss2
        kem_rows.append({"rep": rep, "t_keygen_ns": t1 - t0,
                         "t_encaps_ns": t2 - t1, "t_decaps_ns": t3 - t2,
                         "pk_bytes": len(pk), "kem_ct_bytes": len(ct)})
    with open(outdir / "results_kem.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=kem_rows[0].keys())
        w.writeheader(); w.writerows(kem_rows)

    # One session key for the payload benchmark (KEM already benchmarked).
    kem = oqs.KeyEncapsulation("ML-KEM-512")
    pk = kem.generate_keypair()
    _, ss = kem.encap_secret(pk)
    aes = AESGCM(hkdf_sha256(ss))

    make_schemes = build_schemes()
    rows = []
    root = pathlib.Path("corpus")
    for cdir in sorted(p for p in root.iterdir() if p.is_dir()):
        schemes = make_schemes(cdir.name)
        for fp in sorted(cdir.glob("c?_*.txt")):
            text = fp.read_text(encoding="utf-8")
            n = len(text)
            for name, (enc, dec, canon) in schemes.items():
                # applicability probe (policy c: S1 strict on c2 -> skip)
                try:
                    probe = enc(text)
                except s1_sixbit.OutOfAlphabetError:
                    rows.append({"corpus": cdir.name, "length": n, "scheme": name,
                                 "rep": -1, "status": "NA_alphabet"})
                    continue
                ref = canon(text)
                for _ in range(args.warmup):
                    nonce = secrets.token_bytes(12)
                    aead_ct = aes.encrypt(nonce, enc(text), None)
                    dec(aes.decrypt(nonce, aead_ct, None))
                # NOTE: t_aead_* can be inflated by allocator/cache state
                # left by pure-Python encoders; treat as upper bounds.
                for rep in range(args.reps):
                    nonce = secrets.token_bytes(12)
                    t0 = time.perf_counter_ns(); blob = enc(text)
                    t1 = time.perf_counter_ns(); aead_ct = aes.encrypt(nonce, blob, None)
                    t2 = time.perf_counter_ns(); blob2 = aes.decrypt(nonce, aead_ct, None)
                    t3 = time.perf_counter_ns(); back = dec(blob2)
                    t4 = time.perf_counter_ns()
                    ok = (back == ref)
                    rows.append({"corpus": cdir.name, "length": n, "scheme": name,
                                 "rep": rep, "status": "ok" if ok else "MISMATCH",
                                 "t_encode_ns": t1 - t0, "t_aead_enc_ns": t2 - t1,
                                 "t_aead_dec_ns": t3 - t2, "t_decode_ns": t4 - t3,
                                 "encoded_bytes": len(blob),
                                 "aead_ct_bytes": len(aead_ct)})
    fields = ["corpus", "length", "scheme", "rep", "status", "t_encode_ns",
              "t_aead_enc_ns", "t_aead_dec_ns", "t_decode_ns",
              "encoded_bytes", "aead_ct_bytes"]
    with open(outdir / "results_pipeline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)

    bad = [r for r in rows if r["status"] == "MISMATCH"]
    print(f"pipeline rows: {len(rows)}  kem rows: {len(kem_rows)}  mismatches: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
