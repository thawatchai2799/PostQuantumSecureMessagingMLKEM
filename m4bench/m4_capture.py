"""
m4_capture.py - Start one benchmark run on the board and save its output.

The firmware waits for the letter 'r' on its serial port, runs every cell
once, and ends with a line "end,<ok>,<failed>". This script opens the
ST-LINK virtual COM port, sends 'r', and writes every line it receives to a
text file until the "end" line arrives.

Usage:
    python m4_capture.py <port> [output file]
    e.g.  python m4_capture.py COM5                (Windows)
          python m4_capture.py /dev/ttyACM0        (Linux)
The default output file is m4_raw_<date>_<time>.txt in the current folder.
Needs pyserial:  python -m pip install pyserial
A run takes about two to three minutes; progress is echoed on the screen.
"""

import datetime
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: run  python -m pip install pyserial")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    port = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now().strftime("m4_raw_%Y%m%d_%H%M%S.txt")
    with serial.Serial(port, 115200, timeout=2) as ser, open(out, "w", encoding="ascii", newline="\n") as f:
        time.sleep(0.3)
        ser.reset_input_buffer()
        ser.write(b"r")
        ser.flush()
        print(f"sent start command on {port}; writing to {out}")
        started = time.time()
        lines = 0
        quiet = 0
        while True:
            raw = ser.readline()
            if not raw:
                quiet += 1
                if quiet == 5 and lines == 0:
                    print("no output yet: press the black RESET button on the board, "
                          "then this script sends 'r' again")
                    ser.write(b"r")
                if quiet > 150:                   # 5 minutes without a line
                    print("timed out waiting for the board")
                    return 1
                continue
            quiet = 0
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            f.write(line + "\n")
            lines += 1
            if line.startswith(("info,", "null,", "kem", "end,", "ready", "fault")):
                print(line)
            elif lines % 50 == 0:
                print(f"... {lines} lines, {time.time() - started:.0f} s")
            if line.startswith("end,"):
                ok, failed = line.split(",")[1:3]
                print(f"run complete: {ok} checks passed, {failed} failed, {lines} lines in {time.time() - started:.0f} s")
                return 0 if failed == "0" else 1
            if line.startswith("fault"):
                print("the board reported a fault; stopping")
                return 1


if __name__ == "__main__":
    sys.exit(main())
