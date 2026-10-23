#!/bin/bash
# UKW-Abnahme: 20-s-Checks aller Beispiele (WSL, nice, 1 Kern). Ergebnisse nach tests/results_wsl/
cd "$(dirname "$0")/.."
R=${RESULTS:-tests/results_wsl}; mkdir -p "$R"
SECS=${SECS:-20}
for c in ${CONFIGS:-fm_standard fm_rds_voll fm_mono fm_nachbarkanal fm_pegelrampe}; do
  echo "== check $c ($SECS s)"
  nice -n 10 python3 dablab_run.py check --config "examples/$c.json" --seconds "$SECS" > "$R/check_$c.json" 2> "$R/check_$c.err"
  echo "rc=$?"
  python3 tests/fm_summarize.py "$R/check_$c.json"
  tail -2 "$R/check_$c.err"
done
