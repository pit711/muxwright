#!/bin/bash
# Zeitplan in Echtzeit (live, Ausgabe in Datei mit tsp regulate = Systemuhr): Eintraege zur vollen Minute +1 und +2.
# Ausgewertet wird, wann die Umschaltung "auf Sendung" geht: Bildwechsel (signalstats) -> Paketposition im TS ->
# Wanduhr (Dateigroesse wird waehrend des Laufs alle 0,2 s mit Zeitstempel protokolliert).
# Aufruf: wsl -d dablab -- bash /mnt/g/.../runner/tests/dvb_schedule_live.sh   (braucht die Medien aus dvb_schedule_test.sh)
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNER="$(cd "$HERE/.." && pwd)"
W=/tmp/schedtest
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$W/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
unset DABLAB_SCHEDULE_START DABLAB_CLOCK_OFFSET_S
now=$(date +%s)
t1=$(( (now / 60 + 1) * 60 )); [ $((t1 - now)) -lt 25 ] && t1=$((t1 + 60))
t2=$((t1 + 60))
h1=$(date -d @$t1 +%H:%M); h2=$(date -d @$t2 +%H:%M); h0=$(date -d @$((t1 - 3600)) +%H:%M)
rm -f $W/live.ts $W/sizes.txt
cat > $W/live.json <<EOF
{
  "mode": "dvbt",
  "dvbt": {"mode": "8k", "constellation": "16-QAM", "code_rate": "2/3", "guard": "1/4", "bandwidth_mhz": 8},
  "services": [
    {"key": "plan", "service_id": "0x0040", "name": "LAB Zeitplan", "lcn": 40, "type": "tv",
     "video": {"codec": "mpeg2", "size": "sd", "bitrate_kbps": 4000}, "audio": {"codec": "mp2", "bitrate_kbps": 192},
     "source": {"kind": "schedule", "entries": [
       {"time": "$h0", "kind": "file", "file": "media/video/clipA.mp4", "title": "Vorher"},
       {"time": "$h1", "kind": "testpattern", "pattern": "pal75bars", "title": "Eintrag 1"},
       {"time": "$h2", "kind": "file", "file": "media/video/clipB.mp4", "title": "Eintrag 2"}]}}
  ],
  "output": {"kind": "file", "file": "$W/live.ts", "regulate": true},
  "rf": {"channel": "K25"}
}
EOF
cd $RUNNER
echo "Plan: $h1 ($t1), $h2 ($t2); Start $(date +%T)"
( while true; do [ -f $W/live.ts ] && echo "$(date +%s.%N) $(stat -c %s $W/live.ts)" >> $W/sizes.txt; sleep 0.2; done ) &
SP=$!
dur=$(( t2 + 20 - $(date +%s) ))
timeout -s TERM $dur python3 dablab_run.py live --config $W/live.json 2>$W/live.log >/dev/null || true
kill $SP
grep -o '"now_playing": {[^}]*' $W/live.log | sed 's/"elapsed_s.*//' | head
grep '"lvl": "\(warn\|error\)"' $W/live.log | head -5 || true
ls -l $W/live.ts
python3 - "$W" "$t1" "$t2" <<'PY'
import json, re, subprocess, sys
W, plan = sys.argv[1], [int(sys.argv[2]), int(sys.argv[3])]
ts = W + "/live.ts"
sz = [tuple(map(float, l.split())) for l in open(W + "/sizes.txt") if len(l.split()) == 2]
# Bild-Mittelwerte je Bild (pts) -> Spruenge
out = subprocess.run(["ffmpeg", "-nostdin", "-i", ts, "-map", "0:v:0", "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                      "-f", "null", "-"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode()
fr = []
for m in re.finditer(r"pts_time:([\d.]+)\s*\n.*?YAVG=([\d.]+)", out):
    fr.append((float(m.group(1)), float(m.group(2))))
pk = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,pos",
                                "-of", "json", ts], stdout=subprocess.PIPE).stdout)["packets"]
pos = {round(float(p["pts_time"]), 3): int(p["pos"]) for p in pk if p.get("pts_time") and p.get("pos")}
def wall(bytepos):
    for (ta, sa), (tb, sb) in zip(sz, sz[1:]):
        if sa <= bytepos <= sb and sb > sa:
            return ta + (tb - ta) * (bytepos - sa) / (sb - sa)
jumps = [(b[0], abs(b[1] - a[1])) for a, b in zip(fr, fr[1:]) if abs(b[1] - a[1]) > 8]
print("Bilder:", len(fr), "Spruenge (pts, dY):", [(round(t, 2), round(d, 1)) for t, d in jumps][:12])
for t_plan, (pts, d) in zip(plan, jumps):
    p = pos.get(round(pts, 3))
    w = wall(p) if p is not None else None
    print("Plan %s  Umschaltung im TS pts %.2f s, Byte %s -> geschrieben um %s = %+.2f s gegen Plan"
          % (subprocess.run(["date", "-d", "@%d" % t_plan, "+%T"], stdout=subprocess.PIPE).stdout.decode().strip(), pts, p,
             w and "%.2f" % w, (w - t_plan) if w else float("nan")))
PY
python3 $HERE/dvb_eit_rates.py $W/live.ts --rate 13270588.2 --sizes $W/sizes.txt | grep "^EIT p/f"
