#!/usr/bin/env bash
# daily.sh — local viewer for the ASO dashboard.
#
#   ./daily.sh           rebuild & open the dashboard from committed history
#                        (no API calls, does NOT touch the DB) — the default
#   ./daily.sh pass      run a fresh pass locally (ranks + competitors)
#
# CI is the source of truth: the scheduled workflow runs the real passes,
# commits aso_history.db, and deploys the live dashboard to GitHub Pages. For
# the up-to-date view just open the Pages URL. Use this script for an offline
# look — it regenerates the gitignored index.html from the committed DB.
#
# `pass` mutates the tracked aso_history.db; don't commit that locally (let CI
# own history) or you reintroduce the merge conflicts this setup avoids.
#
# We open whatever file aso_monitor.py reports it wrote (it prints a
# "Dashboard: <path>" line) rather than guessing the name — the report is
# stamped with the latest pass date, which isn't always today.

set -euo pipefail
cd "$(dirname "$0")"

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

if [[ "${1:-}" == "pass" ]]; then
  python3 aso_monitor.py | tee "$LOG"
else
  python3 aso_monitor.py report | tee "$LOG"
fi

REPORT="$(awk '/^Dashboard:/{p=$2} END{print p}' "$LOG")"
if [[ -n "$REPORT" && -f "$REPORT" ]]; then
  echo "Opening $REPORT"
  open "$REPORT"
else
  echo "Couldn't find the generated report path in the output above."
  exit 1
fi
