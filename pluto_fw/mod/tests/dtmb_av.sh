#!/bin/bash
# DTMB-AV-Loopback: ffmpeg-TS (H.264 720p + AAC, CBR = Nutzrate) → dtmb_mod → IQ (15,12 MS/s) → dtmb-sdr → TS;
# Vergleich Byte für Byte (der gesamte TS inkl. Nullpakete muss unverändert zurückkommen) und ffprobe.
# Aufruf (WSL): dtmb_av.sh DTMB_MOD WORKDIR DTMB_SDR_ROOT DTMB_SDR_BUILD [dtmb_mod-Optionen]
set -e
MOD=$1; W=$2; SDR=$3; BUILD=$4; shift 4
OPTS=${*:-"--carriers 3780 --const 64qam --rate 0.6 --pn 945 --ti 720"}
mkdir -p $W; cd $W
RATE=$($MOD $OPTS --print-rates | python3 -c 'import json,sys; print(int(round(json.load(sys.stdin)["ts_bps"])))')
PN=$(echo "$OPTS" | sed -n 's/.*--pn \([0-9]*\).*/\1/p'); PN=${PN:-945}
IDX=$($MOD $OPTS --print-rates | python3 -c 'import json,sys; print(json.load(sys.stdin)["si_index"])')
FBM=c3780; echo "$OPTS" | grep -q -- "--carriers 1" && FBM=c1
ffmpeg -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=25 -f lavfi -i sine=frequency=1000:sample_rate=48000 -t 8 \
  -c:v libx264 -preset veryfast -b:v 6M -maxrate 6M -bufsize 3M -g 25 -pix_fmt yuv420p -c:a aac -b:a 128k \
  -metadata service_name="DABLAB DTMB" -mpegts_service_id 1 -muxrate $RATE -f mpegts av_ref.ts
$MOD -i av_ref.ts -o av.ci16 --sps 2 --quiet $OPTS 2> av_mod.log
PYTHONPATH=$SDR/python python3 -m dtmb.decode --bin-dir $BUILD --input av.ci16 --input-format ci16 --input-rate 15120000 \
  --pn-mode pn$PN --system-info-index $IDX --frame-body-mode $FBM --error-policy continue --output av_dec.ts 2> av_dec.log
rm -f av.ci16
python3 - "$RATE" <<'EOF'
import sys, json, numpy as np
R = np.fromfile('av_ref.ts', np.uint8); D = np.fromfile('av_dec.ts', np.uint8)
n = min(len(R), len(D)) // 188 * 188
same = bool((R[:n] == D[:n]).all())
bad = int(((R[:n].reshape(-1, 188) != D[:n].reshape(-1, 188)).any(1)).sum())
print(json.dumps(dict(muxrate=int(sys.argv[1]), ref_packets=len(R) // 188, decoded_packets=len(D) // 188, compared_packets=n // 188,
                      identical_prefix=same, differing_packets=bad)))
EOF
ffprobe -v error -show_entries program=program_id:stream=codec_name,width,height,sample_rate -of compact av_dec.ts
ffmpeg -v error -i av_dec.ts -f null - && echo "ffmpeg-Dekodierung: fehlerfrei"
