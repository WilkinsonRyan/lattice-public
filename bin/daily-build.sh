#!/bin/zsh
# 05:00 daily via launchd. Does everything slow — rebuilds the digest, sweeps the
# inbox, sweeps the wider world — so that anyone opening Lattice Today before the
# 09:00 notification already sees today's issue rather than yesterday's.
LATTICE="$HOME/Lattice/.venv/bin/lattice"
LOG="$HOME/Lattice/lattice_data/logs/digest.log"
mkdir -p "$(dirname "$LOG")"
echo "$(date '+%Y-%m-%d %H:%M') build start" >>"$LOG"
if ! "$LATTICE" daily >>"$LOG" 2>&1; then
  echo "$(date '+%Y-%m-%d %H:%M') build FAILED" >>"$LOG"; exit 1
fi
echo "$(date '+%Y-%m-%d %H:%M') build ok — $(sed -n '3p' "$HOME/Lattice/lattice_data/digest/latest.md")" >>"$LOG"
