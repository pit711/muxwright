#!/bin/bash
# DVB-Abnahme: (1) live auf stdout mit Echtzeit-Leser + ctl (EIT, Name, Playlist next), (2) UDP-Ausgabe an nc -lu,
# (3) Durchsatz ungebremst. Ergebnisse in tests/results_wsl/dvb_*.txt
# Aufruf (WSL): bash runner/tests/dvb_live_test.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
OUT=$R/tests/results_wsl
mkdir -p $OUT
cd $R
RATE_PL=22394117.6
echo "=== (1) live stdout, Playlist, Echtzeit-Leser 30 s, ctl nach 10 s"
nice -n 10 python3 dablab_run.py live --config examples/dvbt_playlist.json 2> /tmp/dvb_live1.err | \
  python3 tests/dvb_reader.py --rate $RATE_PL --seconds 30 --out /tmp/dvb_live1.ts > /tmp/dvb_live1.reader &
sleep 10
SID=$(head -1 /tmp/dvb_live1.err | python3 -c "import sys,json;print(json.load(sys.stdin)['session'])")
echo "Sitzung $SID"
python3 dablab_run.py ctl --session $SID '{"eit":{"service":"pl1","title":"Neuer Titel per ctl","text":"EIT-Text geaendert"},"name":{"service":"pl1","name":"LAB Playlist NEU","provider":"DABLAB2"},"lcn":{"service":"pl1","lcn":31}}' | tr -d '\n'; echo
sleep 3
python3 dablab_run.py ctl --session $SID '{"playlist":{"service":"pl1","action":"next"},"status":true}' | tr -d '\n' | cut -c1-400; echo
wait
echo "Leser: $(cat /tmp/dvb_live1.reader)"
grep -c '"status"' /tmp/dvb_live1.err | xargs echo "Statuszeilen:"
grep '"status"' /tmp/dvb_live1.err | tail -1 | cut -c1-300
grep -E '"lvl": "(error|warn)"' /tmp/dvb_live1.err | head -5 | cut -c1-300
grep now_playing /tmp/dvb_live1.err | grep -v status | cut -c1-200
echo "Prozesse danach:"; ps -eo pid,cmd | grep -E "tsp|ffmpeg -nostdin" | grep -v grep | head
echo "--- SDT-Versionen/Namen im Mitschnitt:"
tstables --pid 17 --max-tables 400 /tmp/dvb_live1.ts 2>/dev/null | grep -E "Version:|service_name" | sort | uniq -c | head
echo "--- NIT LCN im Mitschnitt:"
tstables --pid 16 --max-tables 400 /tmp/dvb_live1.ts 2>/dev/null | grep -iE "Version:|logical_channel|LCN" | sort | uniq -c | head
echo "--- EIT-Titel im Mitschnitt:"
tstables --pid 18 --max-tables 2000 /tmp/dvb_live1.ts 2>/dev/null | grep -E "Event name" | sort | uniq -c | head
tsanalyze /tmp/dvb_live1.ts 2>/dev/null | grep -E "Transport stream bitrate|Bitrate, based on PCR|Discontinuities|TS packets" | head -4

echo; echo "=== (2) UDP an 127.0.0.1:12345 (nc -lu), 20 s, DVB-C 3 Programme"
python3 - <<'EOF'
import json
c = json.load(open("examples/dvbc_zwei_programme.json"))
c["output"] = {"kind": "udp", "host": "127.0.0.1", "port": 12345, "burst": 7}
json.dump(c, open("/tmp/dvb_udp_cfg.json", "w"), indent=1)
EOF
RATE_C=50870588.2
rm -f /tmp/dvb_udp.ts
(timeout 40 nc -lu 127.0.0.1 12345 > /tmp/dvb_udp.ts) &
NCPID=$!
sleep 1
T0=$(date +%s.%N)
timeout -s TERM 20 nice -n 10 python3 dablab_run.py live --config /tmp/dvb_udp_cfg.json 2> /tmp/dvb_live2.err > /dev/null
T1=$(date +%s.%N)
sleep 1; kill $NCPID 2>/dev/null
BYTES=$(stat -c %s /tmp/dvb_udp.ts)
python3 -c "
b=$BYTES; t=$T1-$T0; r=$RATE_C
print('empfangen %d Byte in %.2f s Laufzeit (inkl. Start) -> %.3f Mb/s; Soll %.3f Mb/s' % (b, t, b*8/t/1e6, r/1e6))"
grep '"status"' /tmp/dvb_live2.err | tail -2 | cut -c1-260
grep -E '"lvl": "(error|warn)"' /tmp/dvb_live2.err | head -5 | cut -c1-300
tsanalyze /tmp/dvb_udp.ts 2>/dev/null | grep -E "Transport stream bitrate|Bitrate, based on PCR|Discontinuities|TS packets|Services:" | head -5
tsp -I file /tmp/dvb_udp.ts -P continuity -O drop 2>&1 | tail -2

echo; echo "=== (3) Durchsatz ungebremst (stdout, Testbild SD, 15 s)"
nice -n 10 python3 dablab_run.py live --config examples/dvbt_testbild_sd.json 2>/dev/null | python3 tests/dvb_reader.py --rate 24128342 --seconds 15 --unpaced
echo "Prozesse danach:"; ps -eo pid,cmd | grep -E "tsp|ffmpeg -nostdin" | grep -v grep | head
