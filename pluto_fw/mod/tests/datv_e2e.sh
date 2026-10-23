#!/bin/bash
# DATV Ende-zu-Ende ohne HF: Runner (TS + dvbs2_mod --sps 2) -> IQ-Datei -> dvbs2_hardrx -> tsanalyze/ffprobe
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"   # Repo-Wurzel (dablab/)
cd $ROOT/runner && source tests/wsl_env.sh 2>/dev/null
T=/tmp/datv; mkdir -p $T
timeout 300 python3 dablab_run.py render --config examples/datv_qo100.json --seconds 12 --out $T/tx.iq --format s16 > $T/render.json 2>$T/render.err
python3 -c "import json;r=json.load(open('$T/render.json'));print('render rc', r.get('ffmpeg_rc'), r.get('tsp_rc'), r.get('summary',{}).get('modulation'))" 2>/dev/null || tail -5 $T/render.err
grep -o "dvbs2_mod [^\"]*" $T/render.err | head -1
ls -la $T/tx.iq | awk '{print "IQ", $5, "Byte =", $5/4/666000, "s"}'
python3 $ROOT/pluto_fw/mod/tests/dvbs2_hardrx.py --iq $T/tx.iq --sps 2 --mod qpsk --rate 2/3 --pilots 0 --rolloff 0.35 -o $T/rx.ts --max 4000000
tsanalyze $T/rx.ts 2>/dev/null | grep -E "Service|Bitrate|Transport|Name" | head -12
ffprobe -v error -show_entries stream=codec_name,width,height,sample_rate -of compact $T/rx.ts
ffmpeg -v error -i $T/rx.ts -f null - && echo "DECODE OK"
