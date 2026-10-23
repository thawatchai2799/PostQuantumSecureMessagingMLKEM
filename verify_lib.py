"""
verify_lib.py - Byte-exact check of the C timing driver for the library
codecs (bench_lib) against the encoders used by the Python pipeline.

For every corpus file and every library scheme it checks that
  (1) bench_lib's encoded bytes equal the Python encoder's bytes, and
  (2) bench_lib decodes the Python-encoded bytes back to the exact text.
Payload figures in the paper come from the Python pipeline and timings
from bench_lib, so this check ties the two together.

Usage: python3 verify_lib.py        (after building bench_lib)
"""
import pathlib
import subprocess
import sys
import tempfile

import heatshrink2
import zstandard

import s3_dict
import s6_unishox

TMP = pathlib.Path(tempfile.mkdtemp(prefix="verify_lib_"))


def c_run(op, sch, inpath, dictpath=None):
    out = TMP / "out.bin"
    cmd = ["./bench_lib", op, sch, str(inpath), str(out)] + ([str(dictpath)] if dictpath else [])
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"bench_lib {op} {sch} {inpath}: {r.stderr.decode()}")
    return out.read_bytes()


def main() -> int:
    dicts = s3_dict.build_dicts(outdir=None)
    zc = zstandard.ZstdCompressor(level=3)
    ux = s6_unishox.UnishoxCodec()
    files = sorted(pathlib.Path("corpus").glob("c?/c?_*.txt"))
    for extra in ("corpus_c4", "corpus_c5"):
        files += sorted(pathlib.Path(extra).glob("c?_*.txt"))
    total = fails = 0
    for fp in files:
        corpus = fp.name.split("_")[0]
        text = fp.read_text(encoding="utf-8")
        raw = text.encode("utf-8")
        dom = s3_dict.DOMAIN_OF.get(corpus, corpus)
        dpath = pathlib.Path("s3_dicts") / f"dict_{dom}_{s3_dict.DICT_SIZE}.zdict"
        py = {
            "s3": (zc.compress(raw), None),
            "s3d": (s3_dict.DictCodec(dicts[dom]).encode(text), dpath),
            "hs8": (heatshrink2.encode(raw, window_sz2=8, lookahead_sz2=4), None),
            "hs11": (heatshrink2.encode(raw, window_sz2=11, lookahead_sz2=4), None),
            "ux": (ux.encode(text), None),
        }
        for sch, (pyb, dp) in py.items():
            ok_enc = c_run("enc", sch, fp, dp) == pyb
            src = TMP / "py.bin"
            src.write_bytes(pyb)
            ok_dec = c_run("dec", sch, src, dp) == raw
            total += 2
            fails += (not ok_enc) + (not ok_dec)
            if not (ok_enc and ok_dec):
                print(f"FAIL {sch} {fp} enc={ok_enc} dec={ok_dec}")
    print(f"library byte-exact checks: {total - fails}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
