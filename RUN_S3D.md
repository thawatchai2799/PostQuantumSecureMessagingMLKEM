# One-shot run

## Windows + WSL2 (recommended)
1. Keep the repository inside the WSL file system (for example `~/repo`).
2. In Windows Explorer open `\\wsl$\<distro>\home\<user>\repo` and
   double-click `run_all.bat`.
3. The console shows a live progress line:
   `[ 37.4%] elapsed 00:02:10 | remaining 00:03:38 | finish ~ 17:05:12 | <stage>`
4. When it says `DONE`, send back the zip file named on the last line
   (`results_phase3/<timestamp>.zip`).

`run_all.bat` runs `python3 run_all.py --reps 100` inside WSL, activating
`venv/` first if one exists. Edit `REPS` or `WSL_REPO_DIR` at the top of
the file if needed.

## Linux / WSL shell
    ./run_all.sh            # same as the .bat; optional argument: reps

## No network?
Place `pg1342.txt` (Gutenberg #1342) and `pg161.txt` (Gutenberg #161) next
to `run_all.py`; they are used instead of downloading.

## What the run produces (all inside the zip)
- `results_pipeline.csv`, `results_kem.csv`, `results_c_latency.csv` (raw, n = reps)
- `results_c4.csv`, `results_dict_size.csv`
- `paper_tables.md` (all tables, Markdown) and `figures/`
- `verify_python.txt`, `verify_c.txt`, `footprint.txt`, `environment.txt`
- `s3_dicts/` (dictionary bytes, IDs, SHA-256), `s2_train*.txt`, `corpus-snapshot/`
- `timings.csv`, `run.log`

## Checks before sending
- `run.log` ends with `DONE` and the integrity lines all say `OK`
- `verify_c.txt`: N/N passed (N = 91 with 7 message lengths)
- `s3_dicts/PROVENANCE_2048.txt`: every dictionary is 2048 bytes
  (a smaller `en` dictionary means the English training text did not download)
- Optional: install `gcc-arm-none-eabi` first so `footprint.txt` includes the Cortex-M4 section
