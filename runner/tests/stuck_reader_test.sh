#!/bin/bash
# SIGTERM bei haengendem Leser: Leser liest 4 s, haelt dann an (SIGSTOP). Runner muss auf SIGTERM trotzdem sauber enden.
cd "$(dirname "$0")/.."
CFG=${1:-examples/minimal_ton.json}
python3 dablab_run.py live --config "$CFG" 2>/tmp/stuck.err | python3 -c "
import os,sys,time,signal
fd=sys.stdin.fileno(); t=time.time()
while time.time()-t<4: os.read(fd,65536)
open('/tmp/stuck.reader','w').write(str(os.getpid()))
os.kill(os.getpid(), signal.SIGSTOP)
" &
sleep 6
SESS=$(python3 -c "import sys,json; sys.path.insert(0,'.'); import dablab_run as d; print(d.SESSIONS)")
RP=$(python3 -c "import json;print(json.load(open('$SESS/'+json.loads(open('/tmp/stuck.err').readline())['session']+'/session.json'))['pid'])")
RD=$(cat /tmp/stuck.reader)
echo "runner pid=$RP (Leser $RD angehalten: $(ps -o stat= -p $RD)), sende SIGTERM"
T0=$(python3 -c "import time;print(time.time())")
kill -TERM $RP
for i in $(seq 1 80); do kill -0 $RP 2>/dev/null || break; sleep 0.1; done
T1=$(python3 -c "import time;print(time.time())")
kill -0 $RP 2>/dev/null && echo "FEHLER: runner laeuft noch" || echo "runner beendet nach $(python3 -c "print(round($T1-$T0,2))") s"
kill -CONT $RD; kill $RD 2>/dev/null
sleep 0.5
S=$(python3 -c "import json;print(json.loads(open('/tmp/stuck.err').readline())['session'][-6:])")
echo "uebrige Prozesse der Sitzung:"; ps -eo pid,ppid,stat,args | grep -- "$S" | grep -v grep || echo keine
tail -1 /tmp/stuck.err | cut -c1-400
