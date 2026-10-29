#!/bin/bash
# Zeitplan fuer ein Radioprogramm (nur Ton): render 40 s, Umschaltung Datei (440 Hz) -> Testton (1 kHz) bei 20 s.
# Braucht die Medien aus dvb_schedule_test.sh. Aufruf: wsl -d dablab -- bash /mnt/g/.../runner/tests/dvb_schedule_radio.sh
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W=/tmp/schedtest
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$W/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
export DABLAB_SCHEDULE_START=12:00:00
cat > $W/radio.json <<'EOF'
{
  "mode": "dvbc",
  "dvbc": {"constellation": "64-QAM", "symbol_rate": 3450000},
  "services": [
    {"key": "rad", "service_id": "0x0050", "name": "LAB Radio Plan", "lcn": 50, "type": "radio",
     "audio": {"codec": "mp2", "bitrate_kbps": 192},
     "source": {"kind": "schedule", "audio": {"kind": "sine", "freq_hz": 1000, "level_dbfs": -18}, "entries": [
       {"time": "11:59", "kind": "file", "file": "media/video/clipA.mp4", "title": "Radio A"},
       {"time": "12:00:20", "kind": "testpattern", "title": "Testton"}]}}
  ],
  "output": {"kind": "file"}, "rf": {"channel": "S21"}
}
EOF
cd $HERE/..
python3 dablab_run.py render --config $W/radio.json --seconds 40 --out $W/radio.ts 2>$W/radio.log | grep -o '"ok": [a-z]*\|"bytes": [0-9]*'
# Grundfrequenz je Sekunde (Nulldurchgaenge) um die Umschaltung
ffmpeg -nostdin -loglevel error -i $W/radio.ts -map 0:a:0 -ac 1 -ar 48000 -f s16le - | python3 -c '
import sys, array
a = array.array("h", sys.stdin.buffer.read())
for s in range(16, 24):
    x = a[s * 48000:(s + 1) * 48000]
    z = sum(1 for i in range(1, len(x)) if (x[i - 1] < 0) != (x[i] < 0))
    print("Sekunde %2d: %4.0f Hz" % (s, z / 2))
'
python3 $HERE/dvb_ts_continuity.py $W/radio.ts | grep -E '"(type|real_deviations|backwards|max_jump_ms)"'
