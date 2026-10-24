#!/bin/bash
# j83b_test.sh – Prüfung dvbc_mod --annex b|c (WSL, GNU Radio 3.10 gr-dtv, numpy):
#  0) Annex A unverändert (md5 der Ausgabe gegen die Werte vor der Erweiterung)
#  1) --print-rates (Nutzraten 26,97035 / 38,81070 Mbit/s)
#  2) QAM-Labels gegen den gr-dtv-Sender (catv_*-Blöcke, unabhängige Implementierung): ffmpeg-TS + Zufalls-TS, alle Steuerwörter
#  3) Loopback dvbc_mod → IQ → tests/j83b_rx.py (eigener Empfänger: Viterbi, Rahmensync, Deinterleaver, RS, Prüfsumme) → TS,
#     paketweise mit dem Original verglichen: sps 1/2, ohne Rauschen, Es/N0 30 dB, Drehung um 90/180/270°
#  4) Annex C: dvbc_mod --annex c → tests/dvbc_demod.py --rolloff 0.13 → TS
#  5) Spektrum (99-%-Bandbreite) bei --sps 2
# Ergebnisse: tests/results/j83b_*.json; BIN=… nimmt ein fertiges dvbc_mod (sonst make im Quellbaum)
set -e
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; T=$M/tests; R=$T/results; W=${WORK:-/tmp/j83test}; mkdir -p $R $W
if [ -z "$BIN" ]; then (cd $M && make -j4 dvbc_mod >/dev/null); BIN=$M/dvbc_mod; fi
cd $W
q() { grep -v -i "buffer_double\|vmcirc" || true; }

# 0) Annex A
python3 - "$BIN" <<'PY' | tee $R/j83b_annex_a_regression.json
import subprocess, hashlib, json, sys
b = sys.argv[1]
exp = {"qam16 sps1": "bc9c90215147d83452fe1bc96595487f", "qam16 sps2": "396de40ed1d8feb004ac01aee1727b6a",
       "qam64 sps1": "9b68d6e9f7f395dfcbbe00eea1b51da0", "qam64 sps2": "be10cb952b4247cf6800f6ae62a62d81",
       "qam256 sps1": "8d1e333d081d827af0ae742fa9490aa5", "qam256 sps2": "43dd338a30d6e331aef6a821a7472611"}
res = {}
for k, v in exp.items():
    qam, sps = k.split()
    out = subprocess.run([b, "-i", "prbs:4000", "-o", "-", "--qam", qam[3:], "--sps", sps[3:], "--quiet"], capture_output=True).stdout
    res[k] = hashlib.md5(out).hexdigest() == v
out = subprocess.run([b, "-i", "prbs:800", "-o", "-", "--qam", "64", "--sr", "5274000", "--sps", "2", "--backoff", "12", "--quiet"], capture_output=True).stdout
res["qam64 sr5274000 sps2 backoff12"] = hashlib.md5(out).hexdigest() == "f93e6b58deea465efc696f73396eb2de"
print(json.dumps({"annex_a_bit_identical": res, "all": all(res.values())}))
PY

# 0b) --threads 2 (Standard) bitgleich zu --threads 1
for c in "--qam 64 --sps 1" "--qam 256 --sps 2"; do
  a=$($BIN --annex b $c --threads 1 -i prbs:3000 -o - --quiet 2>/dev/null | md5sum | cut -c1-32)
  b=$($BIN --annex b $c --threads 2 -i prbs:3000 -o - --quiet 2>/dev/null | md5sum | cut -c1-32)
  echo "{\"case\":\"$c\",\"threads1\":\"$a\",\"threads2\":\"$b\",\"equal\":$([ $a = $b ] && echo true || echo false)}"
done | tee $R/j83b_threads.json

# 1) Raten
for a in "b --qam 64" "b --qam 256" "c --qam 64" "c --qam 256" "a --qam 256"; do $BIN --annex $a --print-rates; done | tee $R/j83b_rates.json

# Test-TS
[ -f t64.ts ] || ffmpeg -y -loglevel error -f lavfi -i testsrc=size=1280x720:rate=30000/1001 -f lavfi -i sine=frequency=1000:sample_rate=48000 \
   -t 10 -c:v mpeg2video -b:v 20M -maxrate 20M -bufsize 4M -c:a mp2 -b:a 256k -f mpegts -muxrate 26970350 -mpegts_service_id 1 t64.ts
[ -f t256.ts ] || ffmpeg -y -loglevel error -f lavfi -i testsrc=size=1280x720:rate=30000/1001 -f lavfi -i sine=frequency=1000:sample_rate=48000 \
   -t 10 -c:v mpeg2video -b:v 30M -maxrate 30M -bufsize 6M -c:a mp2 -b:a 256k -f mpegts -muxrate 38810700 -mpegts_service_id 1 t256.ts
[ -f rand.ts ] || python3 -c "
import numpy as np; r=np.random.default_rng(1); p=r.integers(0,256,(20000,188),dtype=np.uint8)
p[:,0]=0x47; p[:,1]=0x01; p[:,2]=0x00; p[:,3]=0x10|(np.arange(20000)%16); p.tofile('rand.ts')"
head -c $((188*3000)) rand.ts > r3k.ts

# 2) gegen gr-dtv
: > $R/j83b_symbols_vs_grdtv.json
cmp_gr() {  # TS QAM CW
  python3 $T/j83b_grdtv.py tx $1 $2 $3 gr 2>&1 | q
  $BIN --annex b --qam $2 --interleave $3 -i $1 -o /dev/null --dump-sym my.lab --quiet 2>/dev/null
  python3 -c "
import numpy as np, json
a=np.fromfile('gr/6_trellis.u8',np.uint8); b=np.fromfile('my.lab',np.uint8); n=min(len(a),len(b))
print(json.dumps({'ts':'$1','qam':$2,'cw':$3,'gr_symbols':int(len(a)),'dvbc_mod_symbols':int(len(b)),'compared':int(n),'differing':int((a[:n]!=b[:n]).sum())}))" | tee -a $R/j83b_symbols_vs_grdtv.json
}
cmp_gr t64.ts 64 6; cmp_gr t64.ts 64 1; cmp_gr t256.ts 256 6; cmp_gr t256.ts 256 1
for cw in 0 1 2 3 4 5 6 7 8 9 10 12 14; do cmp_gr r3k.ts 64 $cw; cmp_gr r3k.ts 256 $cw; done

# 3) Loopback
: > $R/j83b_loopback.json
lb() {  # TS QAM SPS SNR ROT CW
  $BIN --annex b --qam $2 --sps $3 --interleave $6 -i $1 -o lb.iq --quiet 2>/dev/null
  python3 $T/j83b_rx.py lb.iq --qam $2 --sps $3 --snr $4 --rot $5 --ts-ref $1 | python3 -c "
import json,sys; d=json.load(sys.stdin); d['ts']='$1'; d['cw_tx']=$6; print(json.dumps(d))" | tee -a $R/j83b_loopback.json
}
head -c $((188*12000)) t64.ts > t64s.ts; head -c $((188*12000)) t256.ts > t256s.ts
lb t64s.ts 64 1 0 0 6;  lb t64s.ts 64 2 0 0 6;  lb t64s.ts 64 2 30 0 6;  lb r3k.ts 64 1 30 1 1; lb r3k.ts 64 2 30 2 3; lb r3k.ts 64 1 28 3 14
lb t256s.ts 256 1 0 0 6; lb t256s.ts 256 2 0 0 6; lb t256s.ts 256 2 30 0 6; lb r3k.ts 256 1 30 1 1; lb r3k.ts 256 2 30 2 9; lb r3k.ts 256 1 30 3 14
# Grenzbereich (nur Information: hier arbeiten Viterbi und RS sichtbar)
lb r3k.ts 64 1 21 0 6; lb r3k.ts 256 1 27 0 6

# 4) Annex C
: > $R/j83b_annex_c.json
for qq in 64 256; do
  $BIN --annex c --qam $qq --sps 2 -i r3k.ts -o c.iq --quiet 2>/dev/null
  python3 $T/dvbc_demod.py --iq c.iq --qam $qq --sps 2 --sr 5274000 --rolloff 0.13 --ts r3k.ts --json c.json > /dev/null 2>&1 || true
  python3 -c "import json; d=json.load(open('c.json')); d['annex']='c'; print(json.dumps(d))" | tee -a $R/j83b_annex_c.json
done

# 5) Spektrum
: > $R/j83b_spectrum.json
for a in "b 64" "b 256" "c 64" "c 256"; do
  set -- $a
  $BIN --annex $1 --qam $2 --sps 2 -i r3k.ts -o s.iq --quiet 2>/dev/null
  python3 - $1 $2 <<'PY' | tee -a $R/j83b_spectrum.json
import numpy as np, json, sys
an, qam = sys.argv[1], int(sys.argv[2])
x = np.fromfile("s.iq", np.int16).astype(float); x = (x[0::2] + 1j * x[1::2])[4096:]
sr = {"b64": 5056941, "b256": 5360537}.get(an + str(qam), 5274000); fs = 2 * sr
n = 8192; k = len(x) // n
X = np.fft.fftshift(np.mean(np.abs(np.fft.fft(x[:k * n].reshape(k, n) * np.hanning(n), axis=1)) ** 2, 0)); f = (np.arange(n) - n / 2) * fs / n
c = np.cumsum(X) / X.sum(); bw99 = f[np.searchsorted(c, 0.995)] - f[np.searchsorted(c, 0.005)]
ref = X[np.abs(f) < 0.3 * sr].mean(); edge = 10 * np.log10(X[np.abs(np.abs(f) - 3e6) < 20e3].mean() / ref)
rms = np.sqrt(np.mean(np.abs(x) ** 2)); papr = 20 * np.log10(np.abs(x).max() / rms)
print(json.dumps({"annex": an, "qam": qam, "symbol_rate": sr, "sample_rate": fs, "bw99_hz": float(bw99),
                  "psd_at_3MHz_dbc": float(edge), "rms_dbfs": float(20 * np.log10(rms / 32767)), "papr_db": float(papr)}))
PY
done
