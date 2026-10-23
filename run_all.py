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
CORPORA = ["c1", "c2", "c3"]
REQUIRED_LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]


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
        self.show(force=True)

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
        remaining = 0.0
        for n, c in self.tasks.items():
            if n in self.done:
                continue
            left = c * (1.0 - pfrac) if n == pname else c
            remaining += left * self._ratio(self.category(n), self.done)
        return frac, elapsed, max(0.0, remaining)

    def write_timings(self, path: pathlib.Path):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task", "predicted_s", "measured_s"])
            for n, c in self.tasks.items():
                w.writerow([n, f"{c:.3f}", f"{self.done.get(n, float('nan')):.3f}"])

    def show(self, force: bool = False):
        self._last_draw = time.monotonic()
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
    for tool in ["gcc"]:
        if not which(tool):
            missing.append(f"tool: {tool}")
    mods = {}
    for mod in ["oqs", "zstandard", "cryptography", "pandas", "matplotlib"]:
        try:
            m = __import__(mod)
            mods[mod] = getattr(m, "__version__", "?")
        except Exception as e:  # noqa: BLE001
            missing.append(f"python module: {mod} ({e.__class__.__name__})")
    for f in ["codec.c", "bench_main.c", "s1_sixbit.py", "s2_huffman.py", "s3_dict.py",
              "harness.py", "gen_s2_tables.py", "make_c1_english.py", "make_c2_iot.py",
              "make_c3_uniform.py", "make_c4_modern.py", "make_s2_train_en.py",
              "test_roundtrip.py", "verify_c.py", "analyze.py", "eval_c4.py",
              "eval_dict_size.py", "make_tables.py", "make_figures.py",
              "s2_train_c2.txt", "s2_train_c3.txt"]:
        if not (ROOT / f).exists():
            missing.append(f"file: {f}")
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


# --------------------------------------------------------------------------
# stage 2: corpora + dictionaries
# --------------------------------------------------------------------------
def corpora(out: pathlib.Path):
    c1 = ROOT / "corpus" / "c1"
    need_c1 = any(not (c1 / f"c1_{n}.txt").exists() for n in REQUIRED_LENGTHS)
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
    txt = sh([PY, "s3_dict.py"])
    CON.log("dictionaries: " + "; ".join(l for l in txt.splitlines() if ":" in l))
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            p = ROOT / "corpus" / c / f"{c}_{n}.txt"
            if not p.exists():
                raise StageError(f"missing corpus file {p}")
    prov = (ROOT / "s3_dicts" / "PROVENANCE_2048.txt").read_text(encoding="utf-8")
    for line in prov.splitlines():
        if line.startswith(("en:", "c2:", "c3:", "c4:")) and "bytes=2048" not in line:
            CON.log("WARNING: " + line.split(" sha256")[0] + " (dictionary smaller than the 2048 B cap)")
    shutil.copytree(ROOT / "corpus", out / "corpus-snapshot", dirs_exist_ok=True)
    shutil.copytree(ROOT / "corpus_c4", out / "corpus-snapshot" / "c4", dirs_exist_ok=True)
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
    CON.log(f"verification: {last}; {line[-1].strip()}")


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
    for c, fp in pipeline_plan():
        n = int(fp.stem.split("_")[1])
        schemes = [C_SCHEME_FOR_CORPUS[c]] if c == "c2" else ["s1", C_SCHEME_FOR_CORPUS[c]]
        for sch in schemes:
            plan.append((c, n, sch, fp))
    return plan


def c_latency(out: pathlib.Path, reps: int):
    path = out / "results_c_latency.csv"
    with open(path, "w", newline="") as f:
        f.write("corpus,length,scheme,op,rep,ns\n")
        for c, n, sch, fp in c_latency_plan():
            task = f"c-latency {c} {n} {sch}"
            PROG.set_stage(task)
            t_task = time.monotonic()
            txt = sh(["./bench", "time", sch, str(fp), str(reps)])
            lines = txt.strip().splitlines()[1:]          # skip the bench header line
            for line in lines:
                parts = line.strip().split(",")
                if len(parts) != 3:
                    raise StageError(f"unexpected bench output line: {line!r}")
                f.write(f"{c},{n},{sch},{parts[0]},{parts[1]},{parts[2]}\n")
            PROG.finish(task, time.monotonic() - t_task)
    nrows = sum(1 for _ in open(path)) - 1
    CON.log(f"c-latency rows: {nrows}")


# --------------------------------------------------------------------------
# stage 6: footprint
# --------------------------------------------------------------------------
def footprint(out: pathlib.Path):
    txt = "== x86 -Os codec-only ==\n" + sh(["size", "codec_os.o"])
    if which("arm-none-eabi-gcc") and which("arm-none-eabi-size"):
        sh(["arm-none-eabi-gcc", "-Os", "-mcpu=cortex-m4", "-c", "codec.c", "-o", "codec_arm.o"])
        txt += "\n== ARM Cortex-M4 -Os codec-only ==\n" + sh(["arm-none-eabi-size", "codec_arm.o"])
        CON.log("footprint: x86 and ARM Cortex-M4 collected")
    else:
        txt += "\n(ARM toolchain absent - install gcc-arm-none-eabi to collect the Cortex-M4 footprint)\n"
        CON.log("footprint: x86 only (no ARM toolchain)")
    write(out / "footprint.txt", txt)


# --------------------------------------------------------------------------
# stage 7: summaries, tables, figures
# --------------------------------------------------------------------------
def c_latency_summary(out: pathlib.Path):
    g = defaultdict(list)
    with open(out / "results_c_latency.csv", newline="") as f:
        for r in csv.DictReader(f):
            g[(r["corpus"], int(r["length"]), r["scheme"], r["op"])].append(int(r["ns"]))
    lines = [f"{'corpus':<6}{'len':>7}{'scheme':<7}{'op':<8}{'mean us':>9}{'SD':>8}{'median':>9}"]
    for k in sorted(g):
        v = [x / 1e3 for x in g[k]]
        sd = st.stdev(v) if len(v) > 1 else 0.0
        lines.append(f"{k[0]:<6}{k[1]:>7}{k[2]:<7}{k[3]:<8}{st.mean(v):>9.1f}{sd:>8.1f}{st.median(v):>9.1f}")
    write(out / "summary_c_latency.txt", "\n".join(lines) + "\n")


def summaries(out: pathlib.Path):
    write(out / "summary_pipeline.txt", sh([PY, "analyze.py", "--resdir", str(out)]))
    write(out / "summary_c4.txt", sh([PY, "eval_c4.py"]))
    shutil.move(str(ROOT / "results_c4.csv"), str(out / "results_c4.csv"))
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
    for name in ["results_c4.csv", "results_dict_size.csv", "paper_tables.md",
                 "footprint.txt", "environment.txt", "verify_c.txt", "verify_python.txt",
                 "summary_pipeline.txt", "figures/fig_rq1_bpc.png", "figures/fig_rq3_tradeoff.png"]:
        if not (out / name).exists():
            problems.append(name + " (missing)")
    for name in ["results_c4.csv", "results_dict_size.csv"]:
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
                    "S4-utf8": 0.01e-6}
C_COST_PER_CHAR = 2 * 15e-9


def register_tasks(reps: int, warmup: int):
    PROG.add("preflight", 1)
    PROG.add("build", 3)
    PROG.add("corpora", 6)
    PROG.add("verify", 8)
    PROG.add("kem", 0.5)
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            for s in PY_COST_PER_CHAR:
                if c == "c2" and s == "S1-6bit":
                    PROG.add(f"pipeline {c} {n} {s}", 0.01)
                else:
                    PROG.add(f"pipeline {c} {n} {s}", 0.02 + (reps + warmup) * n * PY_COST_PER_CHAR[s])
    for c in CORPORA:
        for n in REQUIRED_LENGTHS:
            for sch in ([C_SCHEME_FOR_CORPUS[c]] if c == "c2" else ["s1", C_SCHEME_FOR_CORPUS[c]]):
                PROG.add(f"c-latency {c} {n} {sch}", 0.03 + reps * n * C_COST_PER_CHAR)
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
