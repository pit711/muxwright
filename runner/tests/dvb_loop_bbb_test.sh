#!/bin/bash
# Zwei volle Schleifendurchlaeufe Big Buck Bunny (YouTube-Datei media/video/aqz-KE-bpKQ.mp4, 634,6 s):
# render 1300 s (DVB-T QPSK 1/2 1/4, 4,98 Mbit/s, MPEG-2 SD + MP2), danach tsp continuity/pcrverify, PTS/DTS-Schritte,
# Standbild-/Ton-Pruefung um die Schleifenstellen.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
cd $R
SECS=${1:-1300}
MODE=${2:-feeder}
python3 - "$MODE" <<'EOF'
import json, sys
c = {"name": "BBB-Schleife", "mode": "dvbt", "dvbt": {"mode": "8k", "constellation": "QPSK", "code_rate": "1/2", "guard": "1/4", "bandwidth_mhz": 8},
     "services": [{"key": "bbb", "service_id": 1, "name": "BBB LOOP", "type": "tv", "video": {"codec": "mpeg2", "size": "sd", "bitrate_kbps": 3500},
                   "audio": {"codec": "mp2", "bitrate_kbps": 192},
                   "source": {"kind": "file", "file": "media/video/aqz-KE-bpKQ.mp4", "loop": True, "loop_mode": sys.argv[1], "audio": "source"}}],
     "output": {"kind": "stdout"}, "rf": {"channel": "K21"}}
json.dump(c, open("/tmp/dvb_bbb_cfg.json", "w"))
EOF
RATE=$(python3 dablab_run.py validate --config /tmp/dvb_bbb_cfg.json | python3 -c "import sys,json;print(json.load(sys.stdin)['summary']['useful_bitrate_bps'])")
echo "=== BBB $SECS s, loop_mode=$MODE, Rate $RATE"
T0=$(date +%s)
nice -n 10 python3 dablab_run.py render --config /tmp/dvb_bbb_cfg.json --seconds $SECS --out /tmp/dvb_bbb.ts --format ts > /tmp/dvb_bbb_render.json 2>/tmp/dvb_bbb.err
echo "render: $(python3 -c "import json;r=json.load(open('/tmp/dvb_bbb_render.json'));print(r['ok'], r['packets'], 'Pakete', r['ts_seconds'], 's TS')") in $(( $(date +%s) - T0 )) s"
grep -c now_playing /tmp/dvb_bbb.err | xargs echo "Feeder-Durchlaeufe gestartet:"
tsp -I file /tmp/dvb_bbb.ts -P continuity -P pcrverify -O drop 2>&1 | tail -3
tsp -I file /tmp/dvb_bbb.ts -P continuity -O drop 2>&1 | grep -ci "discontinuity" | xargs echo "CC-Diskontinuitaeten:"
python3 tests/dvb_ts_continuity.py /tmp/dvb_bbb.ts --rate $RATE | python3 -c "
import json,sys
r=json.load(sys.stdin)
for s in r['streams']:
    print('%-5s %s: %d Pakete, Schritt %d, echte Abweichungen %d, max. Sprung %.3f ms, rueckwaerts %d, Spanne %.1f s' % (s['type'], s['codec'], s['packets'], s['step_ticks'], s['real_deviations'], s['max_jump_ms'], s['backwards'], s['span_s']))
for pid,v in r['pcr']['pids'].items():
    print('PCR PID %s: %d PCR, %.1f b/s (%+.3f ppm), Jitter max %.1f ns, groesster PCR-Abstand %.1f ms' % (pid, v['pcr_count'], v['measured_bps'], v['deviation_ppm'], v['jitter_max_ns'], v['interval_max_ms']))"
# Schleifenstellen: Ausschnitte +-5 s um 634,6 s und 1269,2 s
for T in 629 1264; do
  ffmpeg -nostdin -v error -ss $T -i /tmp/dvb_bbb.ts -t 10 -c copy -y /tmp/dvb_bbb_seam.ts
  echo "Schleifenstelle um $((T+5)) s: $(python3 tests/dvb_loop_av.py /tmp/dvb_bbb_seam.ts)"
done
rm -f /tmp/dvb_bbb_seam.ts
