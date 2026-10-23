#!/bin/bash
cd ${OUT:-/tmp/dablab}
T=/opt/dablab/mod/src/tests
python3 - <<'EOF'
import numpy as np
r = np.fromfile("rx15.iq", np.int16)
print("clip fraction (|x|>=2047):", np.mean(np.abs(r) >= 2047))
x = r.astype(np.int32) * 8                       # 12 bit → 15 bit
x = np.clip(x, -32768, 32767).astype(np.int16)
x.tofile("/opt/dablab/mod/rx15_norm.iq")
c = x.copy(); c[1::2] = -c[1::2]                 # konjugiert (Spektrumspiegelung)
np.clip(c, -32768, 32767).astype(np.int16).tofile("/opt/dablab/mod/rx15_conj.iq")
# Spektrum-Asymmetrie und Pilotsuche: kontinuierliche Piloten (8k, boosted) im FFT einzelner Symbole
z = (r[0::2] + 1j * r[1::2]).astype(complex)[200000:200000 + 8192 * 200]
N = 8192; acc = np.zeros(N)
for i in range(0, len(z) - N, 10240):
    acc += np.abs(np.fft.fft(z[i:i + N]))**2       # Symbolraster egal: Durchschnittsspektrum
cp2k = [0,48,54,87,141,156,192,201,255,279,282,333,432,450,483,525,531,618,636,714,759,765,780,804,873,888,918,939,942,969,984,1050,1101,1107,1110,1137,1140,1146,1206,1269,1323,1377,1491,1683,1704]
cps = sorted(set(c + 1704 * j for c in cp2k for j in range(4)))
bins_n = [(k - 3408) % N for k in cps]; bins_m = [(3408 - k) % N for k in cps]
def score(b): return np.mean(acc[b]) / np.median(acc[(np.arange(N) - 4096) % N][:])
print("kontinuierliche Piloten – Überhöhung normal: %.2f  gespiegelt: %.2f" % (score(bins_n), score(bins_m)))
f = np.fft.fftfreq(N, 7/64e6)
lo = acc[(f > -3.8e6) & (f < -0.2e6)].mean(); hi = acc[(f > 0.2e6) & (f < 3.8e6)].mean()
print("Leistung unteres/oberes Halbband (dB): %.2f" % (10*np.log10(lo/hi)))
EOF
for v in norm conj; do
  nice -n 10 timeout 900 python3 $T/dvbt_rx_grdtv.py --iq /opt/dablab/mod/rx15_$v.iq --mode 8k --const 16qam --rate 2/3 --guard 1/4 -o /opt/dablab/mod/rx15_$v.ts > /dev/null 2>&1
  echo "== $v: $(ls -la /opt/dablab/mod/rx15_$v.ts | awk '{print $5}') Bytes TS"
  python3 /opt/dablab/mod/tscmp.py /opt/dablab/mod/rx15_$v.ts /opt/dablab/mod/k56/k36.ts 2>&1 | tail -2
  tsanalyze /opt/dablab/mod/rx15_$v.ts 2>/dev/null | grep -E "Service name|Transport errors|Discontinu" | head -3
done
