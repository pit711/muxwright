#!/bin/bash
# Nahtlose Schleife einer Videodatei (DVB): rendert mehrere Schleifendurchlaeufe und prueft PTS/DTS/PCR-Kontinuitaet,
# tsp continuity + pcrverify. Vergleich: loop_mode "feeder" (Standard) gegen "stream_loop" (alter Weg, ffmpeg -stream_loop -1).
# Aufruf (WSL): bash runner/tests/dvb_loop_test.sh [datei] [sekunden]
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
cd $R
FILE=${1:-media/video/clip1.mp4}
SECS=${2:-15}
for MODE in feeder stream_loop; do
  for VC in mpeg2:mp2 h264:aac; do
    V=${VC%%:*}; A=${VC##*:}
    python3 - "$FILE" "$MODE" "$V" "$A" <<'EOF'
import json, sys
f, mode, v, a = sys.argv[1:5]
c = {"name": "Schleifentest", "mode": "dvbt", "dvbt": {"mode": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32", "bandwidth_mhz": 8},
     "services": [{"key": "lp", "service_id": 1, "name": "LOOP", "type": "tv", "video": {"codec": v, "size": "sd", "bitrate_kbps": 4000},
                   "audio": {"codec": a, "bitrate_kbps": 192 if a != "aac" else 128},
                   "source": {"kind": "file", "file": f, "loop": True, "loop_mode": mode, "audio": "source"}}],
     "output": {"kind": "stdout"}, "rf": {"channel": "K21"}}
json.dump(c, open("/tmp/dvb_loop_cfg.json", "w"))
EOF
    DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $DABLAB_RUNTIME/$FILE)
    echo "=== loop_mode=$MODE  $V/$A  Datei $FILE (${DUR} s), ${SECS} s gerendert"
    nice -n 10 python3 dablab_run.py render --config /tmp/dvb_loop_cfg.json --seconds $SECS --out /tmp/dvb_loop.ts --format ts > /tmp/dvb_loop_render.json 2>/tmp/dvb_loop.err
    python3 -c "import json;r=json.load(open('/tmp/dvb_loop_render.json'));print('render ok=%s, %d Pakete, %.2f s TS' % (r['ok'], r['packets'], r['ts_seconds']))"
    python3 tests/dvb_ts_continuity.py /tmp/dvb_loop.ts --rate 24128342.25 | python3 -c "
import json,sys
r=json.load(sys.stdin)
for s in r['streams']:
    print('  %-5s %-10s PID %s: %d Pakete, Schritt %d, Abweichungen %d (echte %d), max. Sprung %.3f ms, rueckwaerts %d, Spanne %.2f s' % (s['type'], s['codec'], s['pid'], s['packets'], s['step_ticks'], s['deviations'], s['real_deviations'], s['max_jump_ms'], s['backwards'], s['span_s']))
for pid,v in r['pcr']['pids'].items():
    print('  PCR PID %s: %d PCR, Rate %.1f b/s (%+.2f ppm), Jitter max %.1f ns, groesster Abstand %.1f ms' % (pid, v['pcr_count'], v['measured_bps'], v['deviation_ppm'], v['jitter_max_ns'], v['interval_max_ms']))
"
    tsp -I file /tmp/dvb_loop.ts -P continuity -P pcrverify -O drop 2>&1 | tail -2 | sed 's/^/  /'
    grep -c "now_playing" /tmp/dvb_loop.err | xargs echo "  Schleifendurchlaeufe (Feeder-Starts):"
  done
done
