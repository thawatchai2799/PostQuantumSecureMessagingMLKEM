#!/usr/bin/env python3
"""
run_all.py - One-shot experiment runner with live progress and ETA.

Runs the complete benchmark (build -> corpora -> verification -> KEM and
pipeline measurements -> C codec latency -> footprint -> summaries ->
tables -> figures -> integrity check) and packs everything into a single
zip file. Nothing else has to be started by hand.

Usage:
    python3 run_all.py [--reps 100] [--warmup 5] [--outroot results_phase3]

Progress line (updated in place):
    [ 37.4%] elapsed 00:02:10 | remaining 00:03:38 | finish ~ 17:05:12 | stage ...

Exit code 0 only if every stage succeeded and the integrity check passed.
All console output is mirrored to <outdir>/run.log.
"""

import argparse
import csv
import datetime as dt
import os
import pathlib
import platform
import secrets
import shutil
import statistics as st
import subprocess
import sys
import time
import zipfile
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

PY = sys.executable
C_SCHEME_FOR_CORPUS = {"c1": "s2en", "c2": "s2c2", "c3": "s2c3"}
DICT_DOMAIN = {"c1": "en", "c2": "c2", "c3": "c3"}
CORPORA = ["c1", "c2", "c3"]
REQUIRED_LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
SLICES = 5
ZSTD_VER = "1.5.7"
ZSTD_TGZ = f"zstd-{ZSTD_VER}.tar.gz"
ZSTD_URL = f"https://github.com/facebook/zstd/releases/download/v{ZSTD_VER}/{ZSTD_TGZ}"
ZSTD_SHA256 = "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3"
ZSTD_DIR = ROOT / "build" / f"zstd-{ZSTD_VER}"
INTEL_DATA = "data.txt.gz"
INTEL_URL = "https://db.csail.mit.edu/labdata/data.txt.gz"
TP = ROOT / "third_party"
LIB_SRC = [str(TP / "heatshrink" / "heatshrink_encoder.c"), str(TP / "heatshrink" / "heatshrink_decoder.c"),
           str(TP / "unishox2" / "unishox2.c")]


def c_schemes(c: str):
    """(paper name, binary, bench scheme, dictionary or None) timed in C."""
    s2 = C_SCHEME_FOR_CORPUS[c]
    rows = [] if c == "c2" else [("S1-6bit", "./bench", "s1", None)]
    rows += [("S2-dom-bit", "./bench", s2, None),
             ("S2-dom-table", "./bench", s2 + "-lut", None),
             ("S3-zstd", "./bench_lib", "s3", None),
             ("S3d-dom", "./bench_lib", "s3d", f"s3_dicts/dict_{DICT_DOMAIN[c]}_2048.zdict"),
             ("S4-utf8", "./bench_lib", "s4", None),
             ("S5-hs8", "./bench_lib", "hs8", None),
             ("S5-hs11", "./bench_lib", "hs11", None),
             ("S6-unishox", "./bench_lib", "ux", None)]
    return rows


# --------------------------------------------------------------------------
# console / logging
# --------------------------------------------------------------------------
class Console:
    def __init__(self):
        self.logf = None
        self._line_open = False
        self._last_status = ""

    def attach(self, path: pathlib.Path):
        self.logf = open(path, "a", encoding="utf-8")

    def _clear_line(self):
        if self._line_open:
            width = shutil.get_terminal_size((100, 20)).columns - 1
            sys.stdout.write("\r" + " " * width + "\r")
            self._line_open = False

    def log(self, msg: str):
        """Permanent line; the progress line is redrawn underneath by the caller."""
        self._clear_line()
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(line, flush=True)
        if self.logf:
            self.logf.write(line + "\n")
            self.logf.flush()
        self._last_status and self.status(self._last_status)

    def status(self, msg: str):
        """Single updating line (no newline)."""
        width = shutil.get_terminal_size((100, 20)).columns - 1
        text = msg[:width].ljust(width)
        sys.stdout.write("\r" + text)
        sys.stdout.flush()
        self._line_open = True
        self._last_status = msg

    def close(self):
        self._clear_line()
        if self.logf:
            self.logf.close()


CON = Console()


# --------------------------------------------------------------------------
# progress / ETA
# --------------------------------------------------------------------------
class Progress:
    """Weighted progress. Each task has a predicted cost (seconds). The ETA
    uses the measured ratio of elapsed time to predicted cost of the tasks
    completed so far, so it self-corrects as the run proceeds."""

    def __init__(self):
        self.tasks = {}          # name -> predicted cost
        self.done = {}           # name -> measured seconds
        self.t0 = None
        self.stage = ""
        self.partial = ("", 0.0)  # (task name, fraction completed) for the running task
        self._last_draw = 0.0

    @staticmethod
    def category(name: str) -> str:
        head = name.split(" ", 1)[0]
        return head if head in ("pipeline", "c-latency") else "fixed"

    def add(self, name: str, cost: float):
        self.tasks[name] = max(cost, 1e-6)

    def start(self):
        self.t0 = time.monotonic()

    @property
    def total_pred(self):
        return sum(self.tasks.values())

    def finish(self, name: str, seconds: float):
        self.done[name] = seconds
        self.partial = ("", 0.0)
        self.show()

    def tick(self, name: str, fraction: float):
        """Partial credit for a long-running task; redraw at most 4x per second."""
        self.partial = (name, min(max(fraction, 0.0), 1.0))
        now = time.monotonic()
        if now - self._last_draw >= 0.25:
            self.show()

    def _ratio(self, cat: str, measured: dict) -> float:
        """Measured seconds per predicted second for one category, blended with
        the prior (1.0) until enough of that category has been observed."""
        pred = sum(self.tasks[n] for n in measured if self.category(n) == cat)
        real = sum(measured[n] for n in measured if self.category(n) == cat)
        if pred <= 0:
            return 1.0
        cat_total = sum(c for n, c in self.tasks.items() if self.category(n) == cat)
        w = min(1.0, pred / max(cat_total * 0.05, 1e-9))
        return w * (real / pred) + (1 - w) * 1.0

    def estimate(self):
        elapsed = time.monotonic() - self.t0
        pname, pfrac = self.partial
        pred_done = sum(self.tasks[n] for n in self.done)
        if pname in self.tasks and pname not in self.done:
            pred_done += self.tasks[pname] * pfrac
        pred_total = self.total_pred
        frac = pred_done / pred_total if pred_total else 0.0
        ratio = {cat: self._ratio(cat, self.done) for cat in ("pipeline", "c-latency", "fixed")}
        remaining = 0.0
        for n, c in self.tasks.items():
            if n in self.done:
                continue
            left = c * (1.0 - pfrac) if n == pname else c
            remaining += left * ratio[self.category(n)]
        return frac, elapsed, max(0.0, remaining)

    def write_timings(self, path: pathlib.Path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task", "predicted_s", "measured_s"])
            for n, c in self.tasks.items():
                w.writerow([n, f"{c:.3f}", f"{self.done.get(n, float('nan')):.3f}"])

    def show(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_draw < 0.25:
            return                      # redraw at most 4x per second
        self._last_draw = now
        frac, elapsed, remaining = self.estimate()
        finish = (dt.datetime.now() + dt.timedelta(seconds=remaining)).strftime("%H:%M:%S")
        CON.status(f"[{100*frac:5.1f}%] elapsed {hms(elapsed)} | remaining {hms(remaining)}"
                   f" | finish ~ {finish} | {self.stage}")

    def set_stage(self, text: str):
        self.stage = text
        self.show()


def hms(seconds: float) -> str:
    seconds = int(round(max(0.0, seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


PROG = Progress()


def run_task(name: str, fn, *args, **kwargs):
    """Execute fn, account its wall time to the progress model."""
    PROG.set_stage(name)
    t = time.monotonic()
    result = fn(*args, **kwargs)
    PROG.finish(name, time.monotonic() - t)
    return result


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class StageError(RuntimeError):
    pass


def sh(cmd, check=True, capture=True, env=None):
    """Run a command; return stdout text. Raise StageError on failure."""
    r = subprocess.run(cmd, capture_output=capture, text=True, env=env)
    if check and r.returncode != 0:
        err = (r.stderr or "") + (r.stdout or "")
        raise StageError(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}\n{err.strip()}")
    return r.stdout if capture else ""


def which(name: str) -> bool:
    return shutil.which(name) is not None


def write(path: pathlib.Path, text: str):
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# stage 0: preflight
# --------------------------------------------------------------------------
def preflight(out: pathlib.Path):
    missing = []
    for tool in ["gcc", "make", "tar"]:
        if not which(tool):
            missing.append(f"tool: {tool}")
    mods = {}
    for mod in ["oqs", "zstandard", "heatshrink2", "cryptography", "pandas", "matplotlib"]:
        try:
            m = __import__(mod)
            mods[mod] = getattr(m, "__version__", "?")
        except Exception as e:  # noqa: BLE001
            missing.append(f"python module: {mod} ({e.__class__.__name__})")
    for f in ["codec.c", "bench_main.c", "bench_lib.c", "s1_sixbit.py", "s2_huffman.py", "s3_dict.py",
              "s5_heatshrink.py", "s6_unishox.py", "verify_lib.py", "make_slices.py", "make_c5_intel.py",
              "third_party/heatshrink/heatshrink_encoder.c", "third_party/unishox2/unishox2.c",
              "harness.py", "gen_s2_tables.py", "make_c1_english.py", "make_c2_iot.py",
              "make_c3_uniform.py", "make_c4_modern.py", "make_s2_train_en.py",
              "test_roundtrip.py", "verify_c.py", "analyze.py", "eval_c4.py",
              "eval_dict_size.py", "make_tables.py", "make_figures.py",
              "s2_train_c2.txt", "s2_train_c3.txt"]:
        if not (ROOT / f).exists():
            missing.append(f"file: {f}")
    if not (ROOT / INTEL_DATA).exists():
        missing.append(f"data file: {INTEL_DATA} (real sensor corpus C5). Download it with:\n"
                       f"      wget {INTEL_URL}\n    and place it next to run_all.py")
    if missing:
        raise StageError("preflight failed:\n  " + "\n  ".join(missing) +
                         "\nInstall the missing items (see README.md) and run again.")
    try:
        import oqs  # noqa: F401
        oqs_ver = oqs.oqs_version()
    except Exception:  # noqa: BLE001
        oqs_ver = "?"
    import zstandard
    env_lines = [
        platform.platform(),
        sh(["uname", "-a"]).strip() if which("uname") else "",
        "",
        sh(["gcc", "--version"]).splitlines()[0],
        f"Python {platform.python_version()}",
        f"liboqs {oqs_ver}",
        f"zstandard {zstandard.__version__} / libzstd {'.'.join(map(str, zstandard.ZSTD_VERSION))}",
        "python modules: " + ", ".join(f"{k} {v}" for k, v in mods.items()),
        "",
    ]
    if which("lscpu"):
        env_lines.append(sh(["lscpu"]))
    write(out / "environment.txt", "\n".join(env_lines))
    CON.log("preflight OK: " + ", ".join(f"{k} {v}" for k, v in mods.items()) + f", liboqs {oqs_ver}")


# --------------------------------------------------------------------------
# stage 1: build
# --------------------------------------------------------------------------
def build(out: pathlib.Path):
    if not (ROOT / "s2_train_PROVENANCE.txt").exists():
        local = ROOT / "pg161.txt"
        cmd = [PY, "make_s2_train_en.py"] + (["--input", str(local)] if local.exists() else [])
        try:
            CON.log("building English training text " + ("from local pg161.txt" if local.exists() else "(downloading Gutenberg #161)"))
            sh(cmd)
        except StageError as e:
            raise StageError(str(e) + "\nIf there is no network access, place pg161.txt "
                             "(Gutenberg #161 plain text) next to run_all.py and run again.") from e
    for f in ROOT.glob("s2_train*.txt"):
        shutil.copy(f, out / f.name)
    shutil.copy(ROOT / "s2_train_PROVENANCE.txt", out / "s2_train_PROVENANCE.txt")
    txt = sh([PY, "gen_s2_tables.py"])
    CON.log("Huffman tables: " + " ".join(txt.split()))
    sh(["gcc", "-O2", "-Wall", "-o", "bench", "codec.c", "bench_main.c"])
    sh(["gcc", "-Os", "-c", "codec.c", "-o", "codec_os.o"])
    CON.log("C codecs built")
    build_libs()


def build_libs():
    """libzstd 1.5.7 from the pinned release tarball, then bench_lib and
    libunishox2.so (heatshrink and Unishox2 are vendored in third_party/)."""
    import hashlib
    import urllib.request
    lib = ZSTD_DIR / "lib" / "libzstd.a"
    if not lib.exists():
        tgz = ROOT / ZSTD_TGZ
        if not tgz.exists():
            CON.log(f"downloading {ZSTD_TGZ}")
            try:
                with urllib.request.urlopen(ZSTD_URL, timeout=120) as resp:
                    tgz.write_bytes(resp.read())
            except Exception as e:  # noqa: BLE001
                raise StageError(f"cannot download {ZSTD_URL} ({e}).\nPlace {ZSTD_TGZ} next to "
                                 "run_all.py and run again.") from e
        digest = hashlib.sha256(tgz.read_bytes()).hexdigest()
        if digest != ZSTD_SHA256:
            raise StageError(f"{ZSTD_TGZ}: SHA-256 {digest} does not match the pinned {ZSTD_SHA256}")
        (ROOT / "build").mkdir(exist_ok=True)
        sh(["tar", "xzf", str(tgz), "-C", str(ROOT / "build")])
        CON.log("building libzstd (about a minute)")
        sh(["make", "-C", str(ZSTD_DIR / "lib"), "libzstd.a", f"-j{os.cpu_count() or 2}"])
    inc = ["-I" + str(ZSTD_DIR / "lib"), "-I" + str(TP / "heatshrink"), "-I" + str(TP / "unishox2")]
    # third-party sources are compiled unmodified; their own warnings are not ours to fix
    objs = []
    for src in LIB_SRC:
        obj = str(ROOT / "build" / (pathlib.Path(src).stem + ".o"))
        sh(["gcc", "-O2", "-w", "-c", src, "-o", obj] + inc)
        objs.append(obj)
    sh(["gcc", "-O2", "-Wall", "-o", "bench_lib", "bench_lib.c"] + objs + [str(lib)] + inc)
    sh(["gcc", "-O2", "-w", "-shared", "-fPIC", "-o", "libunishox2.so", str(TP / "unishox2" / "unishox2.c")])
    CON.log(f"library codecs built (libzstd {ZSTD_VER}, heatshrink, Unishox2)")


# --------------------------------------------------------------------------
# stage 2: corpora + dictionaries
# --------------------------------------------------------------------------
def corpora(out: pathlib.Path):
    c1 = ROOT / "corpus" / "c1"
    need_c1 = (any(not (c1 / f"c1_{n}.txt").exists() for n in REQUIRED_LENGTHS)
               or not (ROOT / "pg1342.txt").exists())
    if need_c1:
        local = ROOT / "pg1342.txt"
        cmd = [PY, "make_c1_english.py"] + (["--input", str(local)] if local.exists() else [])
        try:
            CON.log("building C1 " + ("from local pg1342.txt" if local.exists() else "(downloading Gutenberg #1342)"))
            sh(cmd)
        except StageError as e:
            raise StageError(str(e) + "\nIf there is no network access, place pg1342.txt "
                             "(Gutenberg #1342 plain text) next to run_all.py and run again.") from e
    sh([PY, "make_c2_iot.py"])
    sh([PY, "make_c3_uniform.py"])
    sh([PY, "make_c4_modern.py"])
    CON.log("C5: " + sh([PY, "make_c5_intel.py", "--input", INTEL_DATA]).strip().splitlines()[-1])
    txt = sh([PY, "s3_dict.py"])
    CON.log("dictionaries: " + "; ".join(l for l in txt.splitlines() if ":" in l))
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            p = ROOT / "corpus" / c / f"{c}_{n}.txt"
            if not p.exists():
                raise StageError(f"missing corpus file {p}")
    prov = (ROOT / "s3_dicts" / "PROVENANCE_2048.txt").read_text(encoding="utf-8")
    for line in prov.splitlines():
        if line.startswith(("en:", "c2:", "c3:", "c4:", "c5:")) and "bytes=2048" not in line:
            CON.log("WARNING: " + line.split(" sha256")[0] + " (dictionary smaller than the 2048 B cap)")
    shutil.copytree(ROOT / "corpus", out / "corpus-snapshot", dirs_exist_ok=True)
    shutil.copytree(ROOT / "corpus_c4", out / "corpus-snapshot" / "c4", dirs_exist_ok=True)
    shutil.copytree(ROOT / "corpus_c5", out / "corpus-snapshot" / "c5", dirs_exist_ok=True)
    shutil.copy(ROOT / "s2_train_c5.txt", out / "s2_train_c5.txt")
    CON.log(sh([PY, "make_slices.py"]).strip().splitlines()[-1])
    for c in CORPORA:
        shutil.copy(ROOT / "corpus_slices" / c / "PROVENANCE.txt", out / f"slices_{c}_PROVENANCE.txt")
    shutil.copytree(ROOT / "s3_dicts", out / "s3_dicts", dirs_exist_ok=True)


# --------------------------------------------------------------------------
# stage 3: verification
# --------------------------------------------------------------------------
def verify(out: pathlib.Path):
    txt = sh([PY, "test_roundtrip.py"])
    write(out / "verify_python.txt", txt)
    last = txt.strip().splitlines()[-1] if txt.strip() else ""
    if "PASS: 0" not in txt:
        raise StageError("Python round-trip suite failed: " + last)
    txt = sh([PY, "verify_c.py"])
    write(out / "verify_c.txt", txt)
    line = [l for l in txt.splitlines() if "passed" in l]
    if not line:
        raise StageError("C byte-exact suite produced no summary line")
    a, b = line[-1].split(":")[1].split("passed")[0].strip().split("/")
    if a != b:
        raise StageError("C byte-exact suite failed: " + line[-1])
    txt2 = sh([PY, "verify_lib.py"])
    write(out / "verify_lib.txt", txt2)
    line2 = [l for l in txt2.splitlines() if "passed" in l]
    a2, b2 = line2[-1].split(":")[1].split("passed")[0].strip().split("/")
    if a2 != b2:
        raise StageError("library byte-exact suite failed: " + line2[-1])
    CON.log(f"verification: {last}; {line[-1].strip()}; {line2[-1].strip()}")


# --------------------------------------------------------------------------
# stage 4: KEM + pipeline (same measurement as harness.py, with progress)
# --------------------------------------------------------------------------
def kem_costs(out: pathlib.Path, reps: int, warmup: int):
    import oqs
    for _ in range(warmup):
        k = oqs.KeyEncapsulation("ML-KEM-512")
        pk = k.generate_keypair(); ct, ss = k.encap_secret(pk); k.decap_secret(ct)
    rows = []
    for rep in range(reps):
        k = oqs.KeyEncapsulation("ML-KEM-512")
        t0 = time.perf_counter_ns(); pk = k.generate_keypair(); t1 = time.perf_counter_ns()
        ct, ss = k.encap_secret(pk); t2 = time.perf_counter_ns()
        ss2 = k.decap_secret(ct); t3 = time.perf_counter_ns()
        if ss != ss2:
            raise StageError("KEM shared secret mismatch")
        rows.append({"rep": rep, "t_keygen_ns": t1 - t0, "t_encaps_ns": t2 - t1,
                     "t_decaps_ns": t3 - t2, "pk_bytes": len(pk), "kem_ct_bytes": len(ct)})
    with open(out / "results_kem.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    enc = [r["t_encaps_ns"] / 1e3 for r in rows]
    CON.log(f"KEM: encaps median {st.median(enc):.1f} us, ct {rows[0]['kem_ct_bytes']} B (n={reps})")


PIPE_FIELDS = ["corpus", "length", "scheme", "rep", "status", "t_encode_ns",
               "t_aead_enc_ns", "t_aead_dec_ns", "t_decode_ns",
               "encoded_bytes", "aead_ct_bytes"]


def pipeline_plan():
    plan = []
    for c in CORPORA:
        for fp in sorted((ROOT / "corpus" / c).glob("c?_*.txt"), key=lambda p: int(p.stem.split("_")[1])):
            plan.append((c, fp))
    return plan


def pipeline(out: pathlib.Path, reps: int, warmup: int):
    import oqs
    import s1_sixbit
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import harness

    kem = oqs.KeyEncapsulation("ML-KEM-512")
    pk = kem.generate_keypair()
    _, ss = kem.encap_secret(pk)
    aes = AESGCM(harness.hkdf_sha256(ss))
    make_schemes = harness.build_schemes()

    rows = []
    mismatches = 0
    for c, fp in pipeline_plan():
        text = fp.read_text(encoding="utf-8")
        n = len(text)
        schemes = make_schemes(c)
        for name, (enc, dec, canon) in schemes.items():
            task = f"pipeline {c} {n} {name}"
            PROG.set_stage(task)
            t_task = time.monotonic()
            try:
                enc(text)
            except s1_sixbit.OutOfAlphabetError:
                rows.append({"corpus": c, "length": n, "scheme": name, "rep": -1, "status": "NA_alphabet"})
                PROG.finish(task, time.monotonic() - t_task)
                continue
            ref = canon(text)
            for _ in range(warmup):
                nonce = secrets.token_bytes(12)
                aead_ct = aes.encrypt(nonce, enc(text), None)
                dec(aes.decrypt(nonce, aead_ct, None))
            for rep in range(reps):
                if rep % 10 == 0:
                    PROG.tick(task, (warmup + rep) / (warmup + reps))
                nonce = secrets.token_bytes(12)
                t0 = time.perf_counter_ns(); blob = enc(text)
                t1 = time.perf_counter_ns(); aead_ct = aes.encrypt(nonce, blob, None)
                t2 = time.perf_counter_ns(); blob2 = aes.decrypt(nonce, aead_ct, None)
                t3 = time.perf_counter_ns(); back = dec(blob2)
                t4 = time.perf_counter_ns()
                ok = (back == ref)
                mismatches += (not ok)
                rows.append({"corpus": c, "length": n, "scheme": name, "rep": rep,
                             "status": "ok" if ok else "MISMATCH",
                             "t_encode_ns": t1 - t0, "t_aead_enc_ns": t2 - t1,
                             "t_aead_dec_ns": t3 - t2, "t_decode_ns": t4 - t3,
                             "encoded_bytes": len(blob), "aead_ct_bytes": len(aead_ct)})
            PROG.finish(task, time.monotonic() - t_task)
    with open(out / "results_pipeline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PIPE_FIELDS)
        w.writeheader(); w.writerows(rows)
    CON.log(f"pipeline rows: {len(rows)}  mismatches: {mismatches}")
    if mismatches:
        raise StageError("pipeline produced round-trip mismatches")


# --------------------------------------------------------------------------
# stage 5: C codec latency
# --------------------------------------------------------------------------
def c_latency_plan():
    plan = []
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            for k in range(SLICES):
                fp = ROOT / "corpus_slices" / c / f"{c}_{n}_s{k}.txt"
                for name, binary, sch, dct in c_schemes(c):
                    plan.append((c, n, k, name, binary, sch, dct, fp))
    return plan


def c_latency(out: pathlib.Path, reps: int):
    """Every scheme timed from C, on SLICES independent inputs per cell."""
    path = out / "results_c_latency.csv"
    spath = out / "results_c_sizes.csv"
    with open(path, "w", newline="") as f, open(spath, "w", newline="") as fs:
        f.write("corpus,length,slice,scheme,op,rep,ns\n")
        fs.write("corpus,length,slice,scheme,encoded_bytes\n")
        for c, n, k, name, binary, sch, dct, fp in c_latency_plan():
            task = f"c-latency {c} {n} s{k} {name}"
            PROG.set_stage(task)
            t_task = time.monotonic()
            txt = sh([binary, "time", sch, str(fp), str(reps)] + ([dct] if dct else []))
            for line in txt.strip().splitlines()[1:]:          # skip the header line
                parts = line.strip().split(",")
                if len(parts) != 3:
                    raise StageError(f"unexpected bench output line: {line!r}")
                if parts[0] == "size":
                    fs.write(f"{c},{n},{k},{name},{parts[2]}\n")
                else:
                    f.write(f"{c},{n},{k},{name},{parts[0]},{parts[1]},{parts[2]}\n")
            PROG.finish(task, time.monotonic() - t_task)
    nrows = sum(1 for _ in open(path)) - 1
    CON.log(f"c-latency rows: {nrows} ({SLICES} slices per cell)")


# --------------------------------------------------------------------------
# stage 6: footprint
# --------------------------------------------------------------------------
def footprint_libs() -> str:
    """Code size of the MCU-class library codecs for Cortex-M4 (-Os).
    heatshrink is built in its static configuration (no heap, 2^8 window,
    2^4 lookahead, 32 B input buffer), as it would be on a microcontroller."""
    txt = ""
    cases = [("heatshrink encoder+decoder, static, window 2^8",
              ["-DHEATSHRINK_DYNAMIC_ALLOC=0", "-I" + str(TP / "heatshrink")],
              [str(TP / "heatshrink" / "heatshrink_encoder.c"), str(TP / "heatshrink" / "heatshrink_decoder.c")]),
             ("Unishox2 (compress+decompress)", ["-I" + str(TP / "unishox2")],
              [str(TP / "unishox2" / "unishox2.c")])]
    for label, flags, srcs in cases:
        objs = []
        try:
            for s in srcs:
                o = str(ROOT / "build" / ("arm_" + pathlib.Path(s).stem + ".o"))
                sh(["arm-none-eabi-gcc", "-Os", "-mcpu=cortex-m4", "-mthumb", "-w", "-c", s, "-o", o] + flags)
                objs.append(o)
            txt += f"\n== ARM Cortex-M4 -Os {label} ==\n" + sh(["arm-none-eabi-size"] + objs)
        except StageError as e:
            txt += f"\n== ARM Cortex-M4 -Os {label} ==\n(not built: {str(e).splitlines()[0]})\n"
    return txt


def footprint(out: pathlib.Path):
    txt = "== x86 -Os codec-only ==\n" + sh(["size", "codec_os.o"])
    if which("arm-none-eabi-gcc") and which("arm-none-eabi-size"):
        sh(["arm-none-eabi-gcc", "-Os", "-mcpu=cortex-m4", "-c", "codec.c", "-o", "codec_arm.o"])
        txt += "\n== ARM Cortex-M4 -Os codec-only ==\n" + sh(["arm-none-eabi-size", "codec_arm.o"])
        txt += footprint_libs()
        CON.log("footprint: x86 and ARM Cortex-M4 collected")
    else:
        txt += "\n(ARM toolchain absent - install gcc-arm-none-eabi to collect the Cortex-M4 footprint)\n"
        CON.log("footprint: x86 only (no ARM toolchain)")
    write(out / "footprint.txt", txt)


# --------------------------------------------------------------------------
# stage 6b: instruction counts (deterministic; optional, needs valgrind)
# --------------------------------------------------------------------------
CALLGRIND_CASES = [("s1", "c1"), ("s2en", "c1"), ("s2en-lut", "c1"),
                   ("s2c2", "c2"), ("s2c2-lut", "c2"), ("s2c3", "c3"), ("s2c3-lut", "c3")]
# library codecs: (bench scheme, corpus, dictionary domain or None)
CALLGRIND_LIB = [("s3", "c1", None), ("s3d", "c1", "en"), ("hs8", "c1", None), ("hs11", "c1", None),
                 ("ux", "c1", None), ("s3", "c2", None), ("s3d", "c2", "c2"), ("hs8", "c2", None),
                 ("hs11", "c2", None), ("ux", "c2", None)]


def callgrind(out: pathlib.Path):
    """Instruction counts per character for encode and decode at 20,000 chars.
    baseline = the same binary run on an empty input, so process start-up,
    table construction and file I/O cancel out."""
    if not which("valgrind"):
        write(out / "callgrind.txt", "(valgrind absent - install valgrind to collect instruction counts)\n")
        CON.log("callgrind: skipped (valgrind not installed)")
        return
    tmp = out / "cg_tmp"
    tmp.mkdir(exist_ok=True)
    empty = tmp / "empty.txt"
    empty.write_bytes(b"")

    def irefs(args):
        r = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null",
                            "./bench"] + args, capture_output=True, text=True)
        for line in r.stderr.splitlines():
            if "I" in line and "refs:" in line:
                return int(line.split("refs:")[1].strip().replace(",", ""))
        raise StageError("callgrind produced no instruction count: " + r.stderr[-400:])

    lines = ["# valgrind --tool=callgrind, gcc -O2, 20,000-char inputs.",
             "# instr/char = (I refs - I refs of the empty-input run of the same op) / 20000",
             f"{'scheme':<10}{'op':<8}{'I refs':>12}{'baseline':>12}{'instr/char':>12}"]
    base = {"enc": irefs(["enc", "s1", str(empty), str(tmp / "o.bin")]),
            "dec": irefs(["dec", "s1", str(empty), str(tmp / "o.txt")])}
    for sch, c in CALLGRIND_CASES:
        src = ROOT / "corpus" / c / f"{c}_20000.txt"
        enc = tmp / f"{sch}.bin"
        sh(["./bench", "enc", sch, str(src), str(enc)])
        ie = irefs(["enc", sch, str(src), str(tmp / "o.bin")])
        idn = irefs(["dec", sch, str(enc), str(tmp / "o.txt")])
        lines.append(f"{sch:<10}{'encode':<8}{ie:>12,}{base['enc']:>12,}{(ie - base['enc']) / 20000:>12.2f}")
        lines.append(f"{sch:<10}{'decode':<8}{idn:>12,}{base['dec']:>12,}{(idn - base['dec']) / 20000:>12.2f}")
    # library codecs: per-scheme baseline = the same scheme on an empty message
    # (its encoded form for the decode baseline), so context setup cancels out
    def _irefs_bin(binary, args):
        rr = subprocess.run(["valgrind", "--tool=callgrind", "--callgrind-out-file=/dev/null",
                             binary] + args, capture_output=True, text=True)
        for line in rr.stderr.splitlines():
            if "I" in line and "refs:" in line:
                return int(line.split("refs:")[1].strip().replace(",", ""))
        raise StageError("callgrind produced no instruction count: " + rr.stderr[-400:])

    for sch, c, dom in CALLGRIND_LIB:
        dct = [f"s3_dicts/dict_{dom}_2048.zdict"] if dom else []
        src = ROOT / "corpus" / c / f"{c}_20000.txt"
        enc, enc0 = tmp / f"lib_{sch}.bin", tmp / f"lib_{sch}_0.bin"
        sh(["./bench_lib", "enc", sch, str(src), str(enc)] + dct)
        sh(["./bench_lib", "enc", sch, str(empty), str(enc0)] + dct)
        be = _irefs_bin("./bench_lib", ["enc", sch, str(empty), str(tmp / "o.bin")] + dct)
        bd = _irefs_bin("./bench_lib", ["dec", sch, str(enc0), str(tmp / "o.txt")] + dct)
        ie = _irefs_bin("./bench_lib", ["enc", sch, str(src), str(tmp / "o.bin")] + dct)
        idn = _irefs_bin("./bench_lib", ["dec", sch, str(enc), str(tmp / "o.txt")] + dct)
        lab = f"{sch}/{c}"
        lines.append(f"{lab:<10}{'encode':<8}{ie:>12,}{be:>12,}{(ie - be) / 20000:>12.2f}")
        lines.append(f"{lab:<10}{'decode':<8}{idn:>12,}{bd:>12,}{(idn - bd) / 20000:>12.2f}")
    shutil.rmtree(tmp, ignore_errors=True)
    write(out / "callgrind.txt", "\n".join(lines) + "\n")
    CON.log("callgrind: instruction counts collected")


# --------------------------------------------------------------------------
# stage 7: summaries, tables, figures
# --------------------------------------------------------------------------
def c_latency_summary(out: pathlib.Path):
    g = defaultdict(list)
    with open(out / "results_c_latency.csv", newline="") as f:
        for row in csv.DictReader(f):
            g[(row["corpus"], int(row["length"]), row["scheme"], row["op"], int(row["slice"]))].append(int(row["ns"]))
    cells = defaultdict(list)
    for (c, n, s, op, k), v in g.items():
        cells[(c, n, s, op)].append(st.median(v) / 1e3)
    lines = [f"{'corpus':<6}{'len':>7} {'scheme':<14}{'op':<8}{'median of slice medians':>24}{'min':>9}{'max':>9}"]
    for key in sorted(cells):
        v = cells[key]
        lines.append(f"{key[0]:<6}{key[1]:>7} {key[2]:<14}{key[3]:<8}{st.median(v):>24.2f}{min(v):>9.2f}{max(v):>9.2f}")
    write(out / "summary_c_latency.txt", "\n".join(lines) + "\n")


def summaries(out: pathlib.Path):
    write(out / "summary_pipeline.txt", sh([PY, "analyze.py", "--resdir", str(out)]))
    for c in ("c4", "c5"):
        write(out / f"summary_{c}.txt", sh([PY, "eval_c4.py", "--corpus", c]))
        shutil.move(str(ROOT / f"results_{c}.csv"), str(out / f"results_{c}.csv"))
    write(out / "summary_dict_size.txt", sh([PY, "eval_dict_size.py", "--outdir", str(out)]))
    write(out / "paper_tables.md", sh([PY, "make_tables.py", "--resdir", str(out)]))


def figures(out: pathlib.Path):
    figdir = out / "figures"
    sh([PY, "make_figures.py", "--datadir", str(out), "--outdir", str(figdir)])
    CON.log("figures: " + ", ".join(sorted(p.name for p in figdir.iterdir())))


# --------------------------------------------------------------------------
# stage 8: integrity check and packaging
# --------------------------------------------------------------------------
def integrity(out: pathlib.Path, reps: int):
    def reps_in(path):
        with open(path, newline="") as f:
            vals = {int(r["rep"]) for r in csv.DictReader(f)
                    if r.get("rep", "").lstrip("-").isdigit() and int(r["rep"]) >= 0}
        return (min(vals), max(vals), len(vals)) if vals else (None, None, 0)

    problems = []
    for name in ["results_kem.csv", "results_pipeline.csv", "results_c_latency.csv"]:
        lo, hi, n = reps_in(out / name)
        ok = (lo == 0 and hi == reps - 1 and n == reps)
        CON.log(f"  {name}: reps 0..{hi} ({n} distinct) -> {'OK' if ok else 'STALE/INCOMPLETE'}")
        if not ok:
            problems.append(name)
    for name in ["results_c4.csv", "results_c5.csv", "results_c_sizes.csv", "verify_lib.txt",
                 "results_dict_size.csv", "paper_tables.md",
                 "footprint.txt", "environment.txt", "verify_c.txt", "verify_python.txt",
                 "summary_pipeline.txt", "callgrind.txt", "figures/fig_rq1_bpc.png",
                 "figures/fig_rq2_decoder.png", "figures/fig_rq3_tradeoff.png"]:
        if not (out / name).exists():
            problems.append(name + " (missing)")
    for name in ["results_c4.csv", "results_c5.csv", "results_dict_size.csv"]:
        with open(out / name, newline="") as f:
            bad = [r for r in csv.DictReader(f) if r.get("roundtrip", "ok") != "ok"]
        if bad:
            problems.append(f"{name}: {len(bad)} round-trip mismatches")
    if problems:
        raise StageError("integrity check failed: " + "; ".join(problems))


def package(out: pathlib.Path) -> pathlib.Path:
    zpath = out.parent / f"{out.name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(out.parent))
    return zpath


# --------------------------------------------------------------------------
# cost model for the progress estimate (seconds; self-corrected at run time)
# --------------------------------------------------------------------------
PY_COST_PER_CHAR = {"S1-6bit": 2.0e-6, "S2-dom": 2.5e-6, "S2-en": 2.5e-6,
                    "S3-zstd": 0.05e-6, "S3d-dom": 0.05e-6, "S3d-en": 0.05e-6,
                    "S4-utf8": 0.01e-6, "S5-hs8": 0.1e-6, "S5-hs11": 0.1e-6,
                    "S6-unishox": 0.1e-6}
C_COST_PER_CHAR = {"S1-6bit": 2e-9, "S2-dom-bit": 12e-9, "S2-dom-table": 5e-9, "S3-zstd": 6e-9,
                   "S3d-dom": 8e-9, "S4-utf8": 1e-10, "S5-hs8": 110e-9, "S5-hs11": 190e-9,
                   "S6-unishox": 30e-9}
UX_QUAD = 6.5e-10        # Unishox2 encoding time grows with the square of the length


def register_tasks(reps: int, warmup: int):
    PROG.add("preflight", 1)
    PROG.add("build", 3 if (ZSTD_DIR / "lib" / "libzstd.a").exists() else 60)
    PROG.add("corpora", 6)
    PROG.add("verify", 14)
    PROG.add("kem", 0.5)
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            for s in PY_COST_PER_CHAR:
                if c == "c2" and s == "S1-6bit":
                    PROG.add(f"pipeline {c} {n} {s}", 0.01)
                else:
                    quad = n * n * UX_QUAD if s == "S6-unishox" else 0.0
                    PROG.add(f"pipeline {c} {n} {s}", 0.02 + (reps + warmup) * (n * PY_COST_PER_CHAR[s] + quad))
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            for k in range(SLICES):
                for name, *_ in c_schemes(c):
                    cost = reps * n * C_COST_PER_CHAR[name]
                    if name == "S6-unishox":
                        cost += reps * n * n * UX_QUAD
                    PROG.add(f"c-latency {c} {n} s{k} {name}", 0.01 + cost)
    PROG.add("callgrind", 40)
    PROG.add("footprint", 1)
    PROG.add("summaries", 8)
    PROG.add("figures", 4)
    PROG.add("integrity", 0.5)
    PROG.add("package", 2)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="one-shot experiment runner")
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--outroot", default="results_phase3")
    args = ap.parse_args()
    if args.reps < 2:
        print("--reps must be at least 2", file=sys.stderr)
        return 2

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / args.outroot / ts
    out.mkdir(parents=True, exist_ok=True)
    CON.attach(out / "run.log")
    CON.log(f"run started; reps={args.reps} warmup={args.warmup}; output: {out}")

    register_tasks(args.reps, args.warmup)
    PROG.start()
    PROG.show()
    rc = 0
    try:
        run_task("preflight", preflight, out)
        run_task("build", build, out)
        run_task("corpora", corpora, out)
        run_task("verify", verify, out)
        run_task("kem", kem_costs, out, args.reps, args.warmup)
        pipeline(out, args.reps, args.warmup)          # per-task progress inside
        c_latency(out, args.reps)                      # per-task progress inside
        run_task("footprint", footprint, out)
        run_task("callgrind", callgrind, out)
        run_task("summaries", lambda o: (c_latency_summary(o), summaries(o)), out)
        run_task("figures", figures, out)
        run_task("integrity", integrity, out, args.reps)
        PROG.write_timings(out / "timings.csv")
        zpath = run_task("package", package, out)
        PROG.set_stage("done")
        CON.log(f"DONE in {hms(time.monotonic() - PROG.t0)}")
        CON.log(f"results directory: {out}")
        CON.log(f"send this file:    {zpath}")
    except StageError as e:
        CON.log("FAILED: " + str(e))
        rc = 1
    except KeyboardInterrupt:
        CON.log("interrupted by user")
        rc = 130
    finally:
        CON.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
