#!/bin/bash
# Regressionslauf unter x86 (WSL): DVB-T-Modi gegen gr-dtv-Empfänger (bitexakt), DVB-C gegen numpy-Demodulator,
# Spektrum/PAPR, tsanalyze, Laufzeiten. Ergebnisse als JSON in $OUT/results/.
#   bash tests/run_tests.sh [BUILD_DIR] [WORK_DIR]
set -u
BUILD=${1:-$(cd "$(dirname "$0")/.." && pwd)}
WORK=${2:-/opt/dablab/mod/testwork}
T=$BUILD/tests
mkdir -p "$WORK" "$BUILD/tests/results"
R=$BUILD/tests/results
cd "$WORK"
NICE="nice -n 10"

# Test-TS (ffmpeg): MPEG-2 + MP2, CBR 4 Mb/s, 2 s
if [ ! -f test_in.ts ]; then
  $NICE ffmpeg -loglevel error -y -f lavfi -i "testsrc2=size=320x240:rate=25" -f lavfi -i "sine=frequency=1000:sample_rate=48000" \
    -t 2 -c:v mpeg2video -b:v 1500k -maxrate 1500k -bufsize 1M -c:a mp2 -b:a 128k -mpegts_service_id 1 \
    -metadata service_name="LAB TV" -muxrate 4000000 -f mpegts test_in.ts
fi
tsanalyze --json test_in.ts > "$R/tsanalyze_input.json" 2>/dev/null || true

summary="$R/summary.json"; echo "[" > "$summary"; first=1
add() { [ $first = 1 ] || echo "," >> "$summary"; first=0; cat "$1" >> "$summary"; }

# ---------------- DVB-T ----------------
for cfg in "8k 64qam 2/3 1/32 8" "8k 64qam 7/8 1/4 8" "8k 16qam 3/4 1/8 8" "8k qpsk 1/2 1/16 8" "2k 64qam 5/6 1/32 8" "2k 16qam 2/3 1/4 8" "2k qpsk 7/8 1/8 8" "8k 64qam 2/3 1/32 6" "8k 64qam 2/3 1/32 7"; do
  set -- $cfg; mode=$1; con=$2; rate=$3; gi=$4; bw=$5
  tag="dvbt_${mode}_${con}_${rate/\//-}_${gi/\//-}_${bw}MHz"
  echo "== $tag"
  $NICE "$BUILD/dvbt_mod" -i test_in.ts --mode $mode --const $con --rate $rate --guard $gi --bw $bw --threads 1 -o "$tag.iq" 2> "$tag.log"
  modline=$(head -1 "$tag.log"); doneline=$(tail -1 "$tag.log")
  rt=$(python3 -c "import json;print(json.loads('$doneline')['rt_factor'])")
  rate_hz=$(python3 -c "import json;print(json.loads('$modline')['sample_rate'])")
  ok=false; errs=-1; cmp="{}"
  if true; then
    $NICE timeout 900 python3 "$T/dvbt_rx_grdtv.py" --iq "$tag.iq" --mode $mode --const $con --rate $rate --guard $gi -o "$tag.rx.ts" 2>/dev/null
    cmp=$(python3 - "$tag.rx.ts" test_in.ts <<'EOF'
import numpy as np, sys, json
rx = np.fromfile(sys.argv[1], dtype=np.uint8); ref = np.fromfile(sys.argv[2], dtype=np.uint8)
res = {"rx_bytes": int(len(rx))}
off = next((o for o in range(188) if len(rx) > o + 188*10 and all(rx[o+188*k] == 0x47 for k in range(10))), None)
if off is not None:
    rxp = rx[off:]; rxp = rxp[:len(rxp)//188*188].reshape(-1,188); refp = ref[:len(ref)//188*188].reshape(-1,188)
    try:
        k = next(i for i in range(len(rxp)) if not (rxp[i,1]==0x1f and rxp[i,2]==0xff))
        cand = np.where((refp == rxp[k]).all(axis=1))[0]
        if len(cand):
            r0 = cand[0]-k; n = min(len(rxp), len(refp)-r0)
            res.update({"compared_packets": int(n), "bit_errors": int(np.unpackbits(rxp[:n] ^ refp[r0:r0+n]).sum()), "rx_packets": int(len(rxp))})
    except StopIteration: pass
res["ok"] = res.get("bit_errors", -1) == 0 and res.get("compared_packets", 0) > 1000
print(json.dumps(res))
EOF
)
    tsanalyze --json "$tag.rx.ts" > "$R/tsanalyze_$tag.json" 2>/dev/null || true
  fi
  ana=$($NICE python3 "$T/iq_analyze.py" --iq "$tag.iq" --rate $rate_hz --bw $bw --skip 8448)
  python3 - "$tag" "$modline" "$doneline" "$cmp" "$ana" > "$R/$tag.json" <<'EOF'
import json, sys
tag, mod, done, cmp, ana = sys.argv[1:6]
r = {"test": tag, "modulator": json.loads(mod), "run": json.loads(done), "loop": json.loads(cmp), "signal": json.loads(ana)}
print(json.dumps(r, indent=1))
EOF
  add "$R/$tag.json"
  rm -f "$tag.iq"
done

# ---------------- DVB-C ----------------
for qam in 16 32 64 128 256; do
  for sps in 2 1; do
    tag="dvbc_${qam}qam_sps${sps}"
    echo "== $tag"
    sr=6900000
    $NICE "$BUILD/dvbc_mod" -i test_in.ts --qam $qam --sr $sr --sps $sps -o "$tag.iq" 2> "$tag.log"
    doneline=$(tail -1 "$tag.log"); modline=$(head -1 "$tag.log")
    if [ $sps = 1 ]; then
      # AD9361-FIR-Weg nachbilden: Koeffizienten aus rrc_fir_gen, 2× Interpolation in numpy
      "$BUILD/rrc_fir_gen" --sr $sr -o "rrc_$sr.fir" 2> "rrc_$sr.json"
      python3 - "$tag.iq" "rrc_$sr.fir" "$tag.2x.iq" <<'EOF'
import numpy as np, sys
raw = np.fromfile(sys.argv[1], dtype=np.int16); x = raw[0::2].astype(float) + 1j*raw[1::2].astype(float)
taps = np.array([int(l.split(",")[0]) for l in open(sys.argv[2]) if l[0] in "-0123456789"], float)
up = np.zeros(2*len(x), complex); up[::2] = x
y = np.convolve(up, taps / 32768.0)[:len(up)]
out = np.round(np.stack([y.real, y.imag], 1).reshape(-1)).clip(-32768, 32767).astype(np.int16); out.tofile(sys.argv[3])
EOF
      demod_iq="$tag.2x.iq"
    else demod_iq="$tag.iq"; fi
    $NICE python3 "$T/dvbc_demod.py" --iq "$demod_iq" --qam $qam --sps 2 --ts test_in.ts --sr $sr --json "$tag.demod.json" > /dev/null 2>&1
    ana=$($NICE python3 "$T/iq_analyze.py" --iq "$demod_iq" --rate $((sr*2)) --dvbc-sr $sr --skip 20000)
    python3 - "$tag" "$modline" "$doneline" "$tag.demod.json" "$ana" > "$R/$tag.json" <<'EOF'
import json, sys
tag, mod, done, dj, ana = sys.argv[1:6]
r = {"test": tag, "modulator": json.loads(mod), "run": json.loads(done), "loop": json.load(open(dj)), "signal": json.loads(ana)}
print(json.dumps(r, indent=1))
EOF
    add "$R/$tag.json"
    rm -f "$tag.iq" "$tag.2x.iq"
  done
done
echo "]" >> "$summary"
python3 - "$summary" <<'EOF'
import json, sys
for r in json.load(open(sys.argv[1])):
    l = r["loop"]; s = r["signal"]
    print(f"{r['test']:38s} ok={l.get('ok')} bits_err={l.get('bit_errors', l.get('bit_errors', '?'))} pkts={l.get('compared_packets','?')} rt={r['run'].get('rt_factor')} papr={s.get('papr_db',0):.1f} shoulder={s.get('shoulder_db',0):.1f} mask_nc={s.get('mask_noncritical_ok','-')}")
EOF
