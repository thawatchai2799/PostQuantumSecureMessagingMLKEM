#!/usr/bin/env bash
# run_all.sh - one-shot runner for Linux / WSL shells (same as run_all.bat).
# Usage: ./run_all.sh [REPS]
cd "$(dirname "$0")" || exit 1
if [ -d venv ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi
exec python3 run_all.py --reps "${1:-100}"
