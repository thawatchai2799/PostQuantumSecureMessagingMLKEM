"""
lora_airtime.py - Radio time on air implied by the measured payload sizes.

This is a calculation, not a measurement: it converts the encoded payload
sizes measured by the pipeline into LoRa time on air with the standard
LoRa modem formula (Semtech AN1200.13), for a LoRaWAN EU868 uplink:

    Tsym      = 2^SF / BW
    Tpreamble = (Npreamble + 4.25) * Tsym
    Nsym      = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) * (CR + 4), 0)
    ToA       = Tpreamble + Nsym * Tsym

with BW = 125 kHz, coding rate 4/5 (CR = 1), 8 preamble symbols, explicit
header (IH = 0), payload CRC on, and low-data-rate optimization (DE = 1) at
SF11 and SF12, as LoRaWAN uses. PL is the PHY payload: the application
payload plus 13 bytes of LoRaWAN framing (MHDR 1, FHDR 7, FPort 1, MIC 4).
The application payload of a message is the AEAD ciphertext of the encoded
message plus the 12-byte nonce and 16-byte tag; anything larger than the
data rate's maximum application payload (EU868: 222 B at SF7, 51 B at SF12)
is split into several frames. The 768-byte ML-KEM-512 ciphertext, sent once
per session, is framed the same way.

Time on air is the dominant term of a LoRa node's transmit energy, but the
energy itself depends on the radio, its transmit power and the board; no
energy figure is derived here.

Output: results_airtime.csv
Usage:  python3 lora_airtime.py --resdir <results dir>
"""

import argparse
import csv
import math
import pathlib

BW = 125_000
CR = 1
PREAMBLE = 8
LORAWAN_OVERHEAD = 13
NONCE, TAG = 12, 16
KEM_CT = 768
MAX_APP = {7: 222, 12: 51}          # EU868 DR5 and DR0, no FOpts
SCHEMES = ["S4-utf8", "S2-dom", "S3-zstd", "S3d-dom", "S5-hs8", "S6-unishox"]
CELLS = [("c2", 120), ("c2", 500), ("c5", 120), ("c5", 500), ("c1", 120), ("c1", 500)]


def time_on_air(pl: int, sf: int) -> float:
    de = 1 if sf >= 11 else 0
    tsym = (2 ** sf) / BW
    nsym = 8 + max(math.ceil((8 * pl - 4 * sf + 28 + 16 * 1 - 20 * 0) / (4 * (sf - 2 * de))) * (CR + 4), 0)
    return (PREAMBLE + 4.25) * tsym + nsym * tsym


def uplink(app_bytes: int, sf: int):
    """(frames, total seconds) to send app_bytes of application payload."""
    n = MAX_APP[sf]
    frames = max(1, math.ceil(app_bytes / n))
    sizes = [n] * (frames - 1) + [app_bytes - n * (frames - 1)]
    return frames, sum(time_on_air(s + LORAWAN_OVERHEAD, sf) for s in sizes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resdir", default="data")
    R = pathlib.Path(ap.parse_args().resdir)
    size = {}
    with open(R / "results_pipeline.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["status"] == "ok" and r["rep"] == "0":
                size[(r["corpus"], int(r["length"]), r["scheme"])] = int(r["encoded_bytes"])
    with open(R / "results_c5.csv", newline="") as f:
        for r in csv.DictReader(f):
            size[("c5", int(r["length"]), r["scheme"])] = int(r["encoded_bytes"])
    rows = []
    for sf in (7, 12):
        fr, t = uplink(KEM_CT, sf)
        rows.append({"corpus": "-", "length": "-", "scheme": "ML-KEM-512 ciphertext (once per session)",
                     "sf": sf, "app_bytes": KEM_CT, "frames": fr, "airtime_ms": round(1000 * t, 1)})
        for c, n in CELLS:
            for s in SCHEMES:
                app = size[(c, n, s)] + NONCE + TAG
                fr, t = uplink(app, sf)
                rows.append({"corpus": c, "length": n, "scheme": s, "sf": sf, "app_bytes": app,
                             "frames": fr, "airtime_ms": round(1000 * t, 1)})
    with open(R / "results_airtime.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"SF{r['sf']:<3}{r['corpus']:<4}{str(r['length']):>5}  {r['scheme']:<42}{r['app_bytes']:>6} B "
              f"{r['frames']:>3} frame(s) {r['airtime_ms']:>9.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
