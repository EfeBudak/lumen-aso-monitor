#!/usr/bin/env bash
# daily.sh — one-step ASO daily routine.
#
#   ./daily.sh           run today's pass (ranks + competitors), then open the report
#   ./daily.sh report     just rebuild & open the report from history (no API calls)
#
# We open whatever file aso_monitor.py reports it wrote (it prints a
# "Dashboard: <path>" line) rather than guessing the name — the report is
# stamped with the latest pass date, which isn't always today.

set -euo pipefail
cd "$(dirname "$0")"

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

if [[ "${1:-}" == "report" ]]; then
  python3 aso_monitor.py report | tee "$LOG"
else
  python3 aso_monitor.py | tee "$LOG"
fi

REPORT="$(awk '/^Dashboard:/{p=$2} END{print p}' "$LOG")"
if [[ -n "$REPORT" && -f "$REPORT" ]]; then
  echo "Opening $REPORT"
  open "$REPORT"
else
  echo "Couldn't find the generated report path in the output above."
  exit 1
fi
