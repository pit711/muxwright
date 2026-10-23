#!/bin/bash
# UDP-Ausgabe (tsp -O ip mit regulate) an einen lokalen nc -lu-Empfaenger. Die Rate wird aus dem Wachstum der
# Empfangsdatei im eingeschwungenen Zustand bestimmt (Dateigroesse bei t=5 s und t=25 s), dazu tsanalyze/continuity.
# Aufruf (WSL): bash runner/tests/dvb_udp_test.sh [config] [sekunden]
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
cd $R
CFG=${1:-examples/dvbc_zwei_programme.json}
SECS=${2:-30}
PORT=12345
python3 - "$CFG" <<'EOF'
import json, sys
c = json.load(open(sys.argv[1]))
c["output"] = {"kind": "udp", "host": "127.0.0.1", "port": 12345, "burst": 7}
json.dump(c, open("/tmp/dvb_udp_cfg.json", "w"), indent=1)
EOF
RATE=$(python3 dablab_run.py validate --config /tmp/dvb_udp_cfg.json | python3 -c "import sys,json;print(json.load(sys.stdin)['summary']['useful_bitrate_bps'])")
echo "Konfiguration $CFG, Sollrate $RATE b/s, $SECS s"
rm -f /tmp/dvb_udp.ts
nc -lu 127.0.0.1 $PORT > /tmp/dvb_udp.ts &
NCPID=$!
sleep 0.5
nice -n 10 python3 dablab_run.py live --config /tmp/dvb_udp_cfg.json 2> /tmp/dvb_udp.err > /dev/null &
RPID=$!
sleep 5
S1=$(stat -c %s /tmp/dvb_udp.ts); T1=$(date +%s.%N)
sleep $((SECS - 10))
S2=$(stat -c %s /tmp/dvb_udp.ts); T2=$(date +%s.%N)
sleep 5
kill -TERM $RPID; wait $RPID 2>/dev/null
sleep 0.5; kill $NCPID 2>/dev/null; wait $NCPID 2>/dev/null
python3 -c "
s1,s2,t1,t2,r=$S1,$S2,$T1,$T2,$RATE
m=(s2-s1)*8/(t2-t1)
print('nc -lu: %d Byte in %.3f s (eingeschwungen) -> %.1f b/s, Soll %.1f b/s, Abweichung %+.4f %%' % (s2-s1, t2-t1, m, r, (m/r-1)*100))"
echo "Empfangsdatei: $(stat -c %s /tmp/dvb_udp.ts) Byte, $(( $(stat -c %s /tmp/dvb_udp.ts) % 188 )) Rest mod 188"
grep '"status"' /tmp/dvb_udp.err | tail -2 | cut -c1-230
grep -E '"lvl": "(error|warn)"' /tmp/dvb_udp.err | head -5 | cut -c1-300
tsanalyze /tmp/dvb_udp.ts 2>/dev/null | grep -E "Services:|Transport stream bitrate|Bitrate|TS packets:" | head -4
tsp -I file /tmp/dvb_udp.ts -P continuity -P pcrverify -O drop 2>&1 | tail -3
echo "Prozesse danach:"; ps -eo pid,cmd | grep -E "tsp --bitrate|ffmpeg -nostdin -hide_banner -loglevel warning" | grep -v grep | head -3
