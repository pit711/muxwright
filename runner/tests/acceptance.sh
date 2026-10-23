#!/bin/bash
# Komplette Abnahme; Ergebnisse nach tests/results/
cd "$(dirname "$0")/.."
R=${RESULTS:-tests/results}; mkdir -p $R
[ -f "$(python3 -c "import sys;sys.path.insert(0,'.');import dablab_run as d;print(d.MEDIA)")/pl_titel1.mp3" ] || bash tests/make_playlist_media.sh > /dev/null
echo "== selftest"; python3 dablab_run.py selftest > $R/selftest.json; echo rc=$?
for c in drei_dienste standard_6c mp2_test durchsagen minimal_ton playlist_test; do
  echo "== check $c"; python3 dablab_run.py check --config examples/$c.json --seconds 20 > $R/check_$c.json; echo rc=$?
  python3 tests/summarize.py $R/check_$c.json > $R/check_$c.txt 2>&1
done
echo "== live head -c (unbegrenzter Leser)"
{ T0=$(python3 -c "import time;print(time.time())")
  python3 dablab_run.py live --config examples/drei_dienste.json 2>$R/live_headc.err | head -c 491520000 | wc -c
  T1=$(python3 -c "import time;print(time.time())")
  echo "dauer_s=$(python3 -c "print(round($T1-$T0,2))") fuer 30 s Signal (491520000 Byte)"
  sleep 1; S=$(python3 -c "import json;print(json.loads(open('$R/live_headc.err').readline())['session'][-6:])")
  echo "uebrige Prozesse:"; ps -eo pid,ppid,stat,args | grep -- "$S" | grep -v grep || echo keine
  echo "FIFOs/Sockets:"; ls /tmp | grep -E "dablab-.*$S|dablab_" || echo keine
  tail -1 $R/live_headc.err; } > $R/live_headc.txt 2>&1
echo "== live_test durchsagen"; python3 tests/live_test.py --config examples/durchsagen.json --seconds 30 --outdir /tmp/acc_lt1 > $R/live_durchsagen.json
echo "== live_test standard_6c"; python3 tests/live_test.py --config examples/standard_6c.json --seconds 30 --outdir /tmp/acc_lt2 > $R/live_standard_6c.json
echo "== live_test sigterm"; python3 tests/live_test.py --config examples/minimal_ton.json --seconds 10 --slow-seconds 5 --sigterm --no-decode --outdir /tmp/acc_lt3 > $R/live_sigterm.json
echo "== stuck reader"; bash tests/stuck_reader_test.sh > $R/stuck_reader.txt 2>&1
echo "== matrix"; python3 tests/matrix_test.py --seconds 12 > $R/matrix.json 2> $R/matrix.txt
echo "== level"; python3 tests/level_test.py > $R/level.json
echo "== playlist ctl"; python3 tests/playlist_ctl_test.py > $R/playlist_ctl.json
rm -rf /tmp/acc_lt1 /tmp/acc_lt2 /tmp/acc_lt3
echo FERTIG
