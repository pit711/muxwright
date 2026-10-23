#!/bin/bash
# Schleifenstelle mit ungleich langen Spuren: Testvideos mit Zaehler (jedes Bild verschieden), Ton laenger bzw. kuerzer
# als das Bild. Vergleicht loop_mode feeder (neu) und stream_loop (alt): Standbilder, Schwarzbild, Tonluecken,
# PTS-Spruenge, tsp continuity/pcrverify ueber mehrere Durchlaeufe.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
M=$DABLAB_RUNTIME/media/video
cd $R
mkdir -p $M
[ -f $M/loop_alang.mp4 ] || ffmpeg -nostdin -v error -y -f lavfi -i "testsrc=size=640x360:rate=25" -f lavfi -i "sine=f=700:sample_rate=48000" \
   -filter_complex "[0:v]trim=duration=6[v];[1:a]atrim=duration=6.7[a]" -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -g 25 -c:a aac $M/loop_alang.mp4
[ -f $M/loop_akurz.mp4 ] || ffmpeg -nostdin -v error -y -f lavfi -i "testsrc=size=640x360:rate=30" -f lavfi -i "sine=f=700:sample_rate=44100" \
   -filter_complex "[0:v]trim=duration=6[v];[1:a]atrim=duration=5.4[a]" -map "[v]" -map "[a]" -c:v libx264 -preset veryfast -g 30 -c:a aac $M/loop_akurz.mp4
for F in loop_alang loop_akurz; do
  ffprobe -v error -show_entries stream=codec_type,duration -of compact $M/$F.mp4 | tr '\n' ' '; echo
  for MODE in feeder stream_loop; do
    python3 - "media/video/$F.mp4" "$MODE" <<'EOF'
import json, sys
f, mode = sys.argv[1:3]
c = {"name": "Schleifentest", "mode": "dvbt", "dvbt": {"mode": "8k", "constellation": "64-QAM", "code_rate": "2/3", "guard": "1/32", "bandwidth_mhz": 8},
     "services": [{"key": "lp", "service_id": 1, "name": "LOOP", "type": "tv", "video": {"codec": "h264", "size": "sd", "bitrate_kbps": 4000},
                   "audio": {"codec": "mp2", "bitrate_kbps": 192},
                   "source": {"kind": "file", "file": f, "loop": True, "loop_mode": mode, "audio": "source"}}],
     "output": {"kind": "stdout"}, "rf": {"channel": "K21"}}
json.dump(c, open("/tmp/dvb_loop_cfg.json", "w"))
EOF
    nice -n 10 python3 dablab_run.py render --config /tmp/dvb_loop_cfg.json --seconds 20 --out /tmp/dvb_loop.ts --format ts > /dev/null 2>/tmp/dvb_loop.err
    echo "=== $F, loop_mode=$MODE (20 s, 3 Schleifenstellen)"
    echo "  Aussetzer: $(python3 tests/dvb_loop_av.py /tmp/dvb_loop.ts)"
    python3 tests/dvb_ts_continuity.py /tmp/dvb_loop.ts | python3 -c "
import json,sys
for s in json.load(sys.stdin)['streams']:
    print('  %-5s: %d Pakete, Schritt %d, echte Abweichungen %d, max. Sprung %.3f ms, rueckwaerts %d' % (s['type'], s['packets'], s['step_ticks'], s['real_deviations'], s['max_jump_ms'], s['backwards']))"
    tsp -I file /tmp/dvb_loop.ts -P continuity -P pcrverify -O drop 2>&1 | tail -1 | sed 's/^/  /'
    tsp -I file /tmp/dvb_loop.ts -P continuity -O drop 2>&1 | grep -ci "discontinuity\|error" | xargs echo "  CC-Meldungen:"
  done
done
