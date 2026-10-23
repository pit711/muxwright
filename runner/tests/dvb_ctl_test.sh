#!/bin/bash
# ctl live: EIT-Text, Dienstname/Anbieter, LCN, Playlist next. Mitschnitt mit Echtzeit-Leser, danach Auswertung
# aller Tabellenversionen im Mitschnitt (tstables --all-once fuer jede neue Version).
# Aufruf (WSL): bash runner/tests/dvb_ctl_test.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
cd $R
for CFG in examples/dvbt_testbild_sd.json examples/dvbt_playlist.json; do
  KEY=$(python3 -c "import json;print(json.load(open('$CFG'))['services'][0]['key'])")
  RATE=$(python3 dablab_run.py validate --config $CFG | python3 -c "import sys,json;print(json.load(sys.stdin)['summary']['useful_bitrate_bps'])")
  echo "=== $CFG (Dienst $KEY), 30 s Echtzeit, ctl nach 8 s / 14 s"
  nice -n 10 python3 dablab_run.py live --config $CFG 2> /tmp/dvb_ctl.err | python3 tests/dvb_reader.py --rate $RATE --seconds 30 --out /tmp/dvb_ctl.ts > /tmp/dvb_ctl.reader &
  sleep 8
  SID=$(head -1 /tmp/dvb_ctl.err | python3 -c "import sys,json;print(json.load(sys.stdin)['session'])")
  python3 dablab_run.py ctl --session $SID "{\"eit\":{\"service\":\"$KEY\",\"title\":\"Neuer Titel per ctl\",\"text\":\"EIT-Text geändert (Ü)\"},\"name\":{\"service\":\"$KEY\",\"name\":\"LAB NEU\",\"provider\":\"DABLAB2\"},\"lcn\":{\"service\":\"$KEY\",\"lcn\":77}}" | tr -d '\n '; echo
  sleep 6
  python3 dablab_run.py ctl --session $SID "{\"playlist\":{\"service\":\"$KEY\",\"action\":\"next\"}}" | tr -d '\n ' | cut -c1-200; echo
  wait
  echo "Leser: $(cat /tmp/dvb_ctl.reader)"
  echo "--- SDT (Name/Anbieter je Version):"
  tstables --pid 17 /tmp/dvb_ctl.ts 2>/dev/null | grep -E "Version:|Service name|Provider" | uniq | head -8
  echo "--- NIT (LCN je Version):"
  tstables --pid 16 --xml-output - /tmp/dvb_ctl.ts 2>/dev/null | grep -E "<NIT |logical_channel_number=" | uniq | head -8
  echo "--- EIT p/f (Ereignisse je Version):"
  tstables --pid 18 /tmp/dvb_ctl.ts 2>/dev/null | grep -E "Version:|Section:|Event name|Description" | uniq | head -24
  tsp -I file /tmp/dvb_ctl.ts -P continuity -O drop 2>&1 | tail -1
  grep -E '"lvl": "(error|warn)"' /tmp/dvb_ctl.err | head -3 | cut -c1-250
done
