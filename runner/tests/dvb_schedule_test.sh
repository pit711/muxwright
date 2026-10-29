#!/bin/bash
# Zeitplan-Test (Quellenart "schedule"): render mit gesetzter Zeitplan-Uhr (DABLAB_SCHEDULE_START), Umschaltungen bei
# +20 s / +40 s / +60 s, danach Standbilder vor/nach jeder Umschaltung, Continuity und EIT p/f + schedule.
# Aufruf: wsl -d dablab -- bash /mnt/g/.../runner/tests/dvb_schedule_test.sh [Sekunden]
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$(cd "$HERE/.." && pwd)"
SEC=${1:-80}
W=/tmp/schedtest
rm -rf $W && mkdir -p $W/runtime/media/video $W/runtime/playlists $W/frames $W/img
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$W/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
mk() {  # Name Dauer Muster Tonfrequenz Text
  ffmpeg -nostdin -loglevel error -f lavfi -i "$3=size=640x360:rate=25" -f lavfi -i "sine=frequency=$4:sample_rate=48000" \
    -t $2 -vf "drawtext=fontfile=$FONT:text='$5 %{pts\:hms}':fontsize=48:fontcolor=yellow:box=1:boxcolor=black:x=40:y=150" \
    -metadata title="$5" -c:v libx264 -preset ultrafast -c:a aac -shortest -y $W/runtime/media/video/$1.mp4
}
mk clipA 12 testsrc2 440 "CLIP A"
mk clipB 8 rgbtestsrc 880 "CLIP B"
mk clipC 6 mandelbrot 660 "CLIP C"
cat > $W/runtime/playlists/abend.json <<'EOF'
{"name": "abend", "items": [{"file": "media/video/clipC.mp4", "title": "Playlist C"}, {"file": "media/video/clipA.mp4", "title": "Playlist A"}]}
EOF
# Simulierte Startzeit der Zeitplan-Uhr: 10:00:00 (heute, Ortszeit)
export DABLAB_SCHEDULE_START=10:00:00
cat > $W/sched.json <<'EOF'
{
  "mode": "dvbt",
  "dvbt": {"mode": "8k", "constellation": "16-QAM", "code_rate": "2/3", "guard": "1/4", "bandwidth_mhz": 8},
  "ts": {"transport_stream_id": "0x0105", "original_network_id": "0xFF01", "network_name": "DABLAB"},
  "services": [
    {"key": "plan", "service_id": "0x0040", "name": "LAB Zeitplan", "lcn": 40, "type": "tv",
     "video": {"codec": "mpeg2", "size": "sd", "bitrate_kbps": 4000},
     "audio": {"codec": "mp2", "bitrate_kbps": 192},
     "source": {"kind": "schedule", "filler": "smptebars", "entries": [
       {"time": "09:59:30", "kind": "file", "file": "media/video/clipA.mp4", "title": "Morgenmagazin", "text": "Clip A in Schleife"},
       {"time": "10:00:20", "kind": "testpattern", "pattern": "pal75bars", "title": "Testbild-Pause"},
       {"time": "10:00:40", "kind": "file", "file": "media/video/clipB.mp4", "title": "Nachrichten", "loop": false},
       {"time": "10:01:00", "kind": "playlist", "playlist": "abend", "title": "Abendprogramm", "text": "Playlist abend"},
       {"time": "20:15", "days": [1, 2, 3, 4, 5], "kind": "file", "file": "media/video/clipB.mp4", "title": "Spielfilm"},
       {"time": "22:00", "days": [6, 7], "kind": "testpattern", "title": "Wochenend-Testbild"}
     ]},
     "eit": {"title": "LAB Zeitplan", "text": "Füller"}}
  ],
  "output": {"kind": "file"},
  "rf": {"channel": "K25"}
}
EOF
cd $RUNNER
python3 dablab_run.py validate --config $W/sched.json | head -c 600; echo
python3 dablab_run.py render --config $W/sched.json --seconds $SEC --out $W/out.ts 2>$W/render.log | tail -c 400; echo
ls -l $W/out.ts
# Standbilder: Video-Startzeit des TS + Sekunde x (Umschaltung bei 20/40/60 s Stromzeit)
for t in 1 5 19.6 20.4 39.6 40.4 48.6 49.4 59.6 60.4 66.6 67.4; do
  ffmpeg -nostdin -loglevel error -ss $t -i $W/out.ts -map 0:v:0 -frames:v 1 -vf scale=320:-2 -y $W/frames/f_$t.png
done
ls $W/frames
python3 $HERE/dvb_ts_continuity.py $W/out.ts 2>&1 | tail -5 || true
# EIT p/f (0x4E) und schedule (0x50, segmentiert: nur mit --all-sections sichtbar), Wiederholabstaende
tstables --pid 18 --tid 0x4E $W/out.ts > $W/eit_pf.txt 2>&1 || true
tstables --pid 18 --tid 0x50 --all-sections --max-tables 25 $W/out.ts > $W/eit_sched.txt 2>&1 || true
tstables --pid 17 --max-tables 1 $W/out.ts | grep -i "EIT"
python3 $HERE/dvb_eit_rates.py $W/out.ts --rate 13270588.2
