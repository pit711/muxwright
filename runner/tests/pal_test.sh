#!/bin/bash
# PAL-Abnahme (WSL, ohne Geraet): render (Schleife), check (beide Verfahren), Vergleich mit der Referenzdatei des
# Modulator-Agenten, live-Mitschnitte (Testbild + Video) demoduliert, Durchsatz je Worker-Zahl.
# Aufruf: wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/runner/tests/pal_test.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=$ROOT/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=$ROOT/runner
OUT=$R/tests/results_wsl
C=$DABLAB_RUNTIME/checks
cd $R
{
echo "# PAL-Abnahme $(date '+%F %T')"
echo; echo "## render (Standbild-Schleife K36, 8 MS/s)"
nice -n 10 python3 dablab_run.py render --config examples/pal_k36_testbild.json --out /tmp/pal_k36.s16 --format s16 | python3 -c "
import json,sys; r=json.load(sys.stdin); print({k:r[k] for k in ('ok','bytes','samples','loop_s','sample_rate','lo_hz','vision_hz','elapsed_s')}); print('Spektrum', r['spectrum']['rel_db'], 'RMS', r['spectrum']['rms_dbfs'], 'dBFS, Spitze', r['spectrum']['peak_dbfs'])"
REF=${OUT:-/tmp/dablab}/pal_k36.iq
if [ -f $REF ]; then
python3 - $REF <<'EOF'
import sys, numpy as np
a = np.fromfile("/tmp/pal_k36.s16", np.int16).astype(float); b = np.fromfile(sys.argv[1], np.int16).astype(float)
n = min(len(a), len(b)); a, b = a[:n], b[:n]
x = a[0::2] + 1j * a[1::2]; y = b[0::2] + 1j * b[1::2]
g = np.vdot(y, x) / np.vdot(y, y)
err = x - g * y
print("Referenzdatei des Modulator-Agenten (am LG-TV gesendet, anderer Textaufbau im Bild): Laenge %d/%d Samples, Gleichheit %.1f %% der Werte, SNR %.1f dB"
      % (len(x), len(y), 100 * np.mean(a == b), 10 * np.log10(np.mean(np.abs(g * y) ** 2) / max(np.mean(np.abs(err) ** 2), 1e-30))))
EOF
fi
echo "Uebernahme 1:1 (gleiches Bild wie das Original): $(nice -n 10 python3 tests/pal_port_check.py)"
echo; echo "## check (FFT-Schleife und Strom-Encoder)"
nice -n 10 python3 dablab_run.py check --config examples/pal_k36_testbild.json > /tmp/palchk.json
python3 - <<'P'
import json; r=json.load(open("/tmp/palchk.json"))
print("ok", r.get("ok"), "Abweichungen", r.get("mismatches"), "Dauer", r.get("elapsed_s"), "s")
for m,d in r.get("methods",{}).items():
    print(" ", m, {k:d.get(k) for k in ("line_rate_hz","blank_rel","white_rel","sound_rel_peak_db","audio_tone_hz","deviation_peak_hz","chroma_rel_db","luma_correlation","loop_seam_percentile","clipped_samples")})
    print("    PNG", d.get("png"))
P
for SYS in DK I; do
  python3 -c "
import json; c=json.load(open('examples/pal_k36_testbild.json')); c['pal']['system']='$SYS'; c['pal']['image']['text2']='PAL $SYS'
json.dump(c, open('/tmp/pal_$SYS.json','w'))"
  nice -n 10 python3 dablab_run.py check --config /tmp/pal_$SYS.json --method fft > /tmp/palchk_$SYS.json
  python3 -c "
import json; r=json.load(open('/tmp/palchk_$SYS.json')); d=r['methods']['fft']
print('Norm $SYS: ok', r['ok'], r['mismatches'], 'Versatz', r['summary']['loop']['offset_hz'], {k:d.get(k) for k in ('blank_rel','sound_rel_peak_db','audio_tone_hz','luma_correlation')})"
done
echo; echo "## live: Durchsatz (7 MS/s, ungebremst 15 s)"
for W in 1 2 3; do
  timeout -s TERM 40 nice -n 10 python3 dablab_run.py live --config examples/pal_live_testbild.json --workers $W 2>/tmp/pallive.err | python3 tests/dvb_reader.py --rate 224000000 --seconds 15 --unpaced | sed "s/^/  $W Worker: /"
  sleep 1
done
echo "  Restprozesse: $(ps -eo cmd | grep -E 'dablab_run.py live|size=720x576' | grep -v grep | wc -l)"
echo; echo "## live: Mitschnitt demoduliert"
for CFG in pal_live_testbild pal_live_video; do
  timeout -s TERM 30 nice -n 10 python3 dablab_run.py live --config examples/$CFG.json 2>/tmp/pallive_$CFG.err | head -c $((7000000*4*3)) > /tmp/pallive_$CFG.s16
  python3 tests/pal_live_capture.py --iq /tmp/pallive_$CFG.s16 --fs 7e6 --carrier=-2.1e6 --skip-s 2.0 --png $C/pal_live_$CFG.png | python3 -c "
import json,sys; d=json.load(sys.stdin); print('$CFG:', {k:d.get(k) for k in ('line_rate_hz','blank_rel','white_rel','sound_rel_peak_db','audio_tone_hz','deviation_peak_hz','deviation_rms_hz','chroma_rel_db','vsync_lines','rms_dbfs','peak','png')})"
  sleep 1
done
echo "  Restprozesse: $(ps -eo cmd | grep -E 'dablab_run.py live|size=720x576' | grep -v grep | wc -l)"
} 2>&1 | tee $OUT/pal_acceptance.txt
