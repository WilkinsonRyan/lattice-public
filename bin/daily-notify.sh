#!/bin/zsh
# 09:00 daily via launchd. The slow work already happened at 05:00; this only
# refreshes the counts (a database read, under a second) so the banner cannot
# quote a number that changed while the user was awake, then posts the notification.
# The notification has to come FROM the app bundle — one posted by osascript
# belongs to Script Editor, and clicking it opens Script Editor.
LATTICE="$HOME/Lattice/.venv/bin/lattice"
APP="$HOME/Applications/Lattice Digest.app"
LOG="$HOME/Lattice/lattice_data/logs/digest.log"
mkdir -p "$(dirname "$LOG")"
"$LATTICE" digest --write --quiet >>"$LOG" 2>&1
/usr/bin/open -a "$APP" --args notify >>"$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M') notified — $(sed -n '3p' "$HOME/Lattice/lattice_data/digest/latest.md")" >>"$LOG"
