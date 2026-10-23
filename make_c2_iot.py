"""
make_c2_iot.py — Build corpus C2 (IoT JSON sensor payloads).

Generates realistic MQTT-style telemetry: a stream of JSON records with
device IDs, ISO timestamps, and float sensor readings. Fully seeded, so
the paper only needs to report the seed + schema for reproducibility.

Schema per record (typical smart-building node):
  {"dev":"sn-XXXX","ts":"2026-08-29T12:34:56Z","seq":N,
   "temp_c":23.41,"rh_pct":51.2,"pm25":8.3,"batt_v":3.71,"rssi":-67}

Records are concatenated NDJSON-style (one per line) and the stream is
cut to exactly N characters, mirroring how a gateway batches telemetry.

Output: corpus/c2/c2_{120,500,1000,2000,3000,5000,20000}.txt
(120 chars ≈ one telemetry record: the single-record regime)
"""

import argparse
import datetime as dt
import json
import pathlib
import random

LENGTHS = [120, 500, 1000, 2000, 3000, 5000, 20000]
SEED = 20260829  # fixed; report in paper
BASE_TS = dt.datetime(2026, 8, 29, 8, 0, 0)


def gen_stream(rng: random.Random, min_chars: int) -> str:
    lines = []
    total = 0
    seq = 0
    devices = [f"sn-{rng.randint(1000, 9999)}" for _ in range(4)]
    while total < min_chars:
        ts = (BASE_TS + dt.timedelta(seconds=15 * seq)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = {
            "dev": rng.choice(devices),
            "ts": ts,
            "seq": seq,
            "temp_c": round(rng.gauss(23.0, 1.5), 2),
            "rh_pct": round(rng.uniform(35, 65), 1),
            "pm25": round(abs(rng.gauss(9, 4)), 1),
            "batt_v": round(rng.uniform(3.3, 4.1), 2),
            "rssi": -rng.randint(40, 95),
        }
        line = json.dumps(rec, separators=(",", ":"))
        lines.append(line)
        total += len(line) + 1  # +1 for newline
        seq += 1
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="corpus/c2")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    stream = gen_stream(rng, max(LENGTHS) + 100)

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for n in LENGTHS:
        slice_ = stream[:n]
        assert len(slice_) == n
        p = outdir / f"c2_{n}.txt"
        p.write_text(slice_, encoding="utf-8")
        print(f"  wrote {p}  ({n} chars)")

    (outdir / "PROVENANCE.txt").write_text(
        f"generator: make_c2_iot.py\nseed: {args.seed}\n"
        "schema: dev,ts,seq,temp_c,rh_pct,pm25,batt_v,rssi (NDJSON)\n",
        encoding="utf-8",
    )
    print("C2 done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
