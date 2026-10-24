#!/bin/bash
# UDP-Eingang + Echtzeitbremse (x86/WSL): TS mit fester Rate per UDP (7 Pakete je Datagramm) an dvbt2_mod -i udp://,
# Ausgabe --realtime in eine IQ-Datei, danach Dekodierung mit dem unabhängigen Empfänger (falls vorhanden).
#   tests/dvbt2_udp_test.sh [BIN] [TS] [PRESET] [SEKUNDEN]
BIN=${1:-/opt/dablab/mod/t2/dvbt2_mod}; TS=${2:-${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx/ref/orig_de.ts}; PR=${3:-de}; SECS=${4:-8}
OUT=${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_loop; mkdir -p $OUT; PORT=12345
RATE=$($BIN --preset $PR --print-rates | python3 -c "import json,sys; print(json.load(sys.stdin)['ts_bps'])")
SEND=$(python3 -c "print(min(float('$RATE') * 0.995, 1e12))")   # etwas unter der Nutzrate → Modulator stopft Nullpakete
python3 - "$TS" $PORT "$SEND" $((SECS + 4)) <<'EOF' &
import socket, sys, time
ts, port, rate, dur = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
d = open(ts, "rb").read(); s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
step = 7 * 188; per = step * 8 / rate; t0 = time.time(); i = 0; n = 0
while time.time() - t0 < dur:
    if i + step > len(d): i = 0
    s.sendto(d[i:i + step], ("127.0.0.1", port)); i += step; n += 1
    lag = t0 + n * per - time.time()
    if lag > 0: time.sleep(lag)
print(f"gesendet: {n * 7} Pakete in {time.time() - t0:.1f} s ({n * step * 8 / (time.time() - t0) / 1e6:.3f} Mbit/s)", file=sys.stderr)
EOF
SP=$!
$BIN --preset $PR -i udp://127.0.0.1:$PORT --udp-buffer 2000 -o $OUT/udp_$PR.iq --realtime --seconds $SECS 2>&1 | tail -2
wait $SP
if [ -x ${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx/decode.sh ]; then
  (cd ${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx && ./decode.sh $OUT/udp_$PR.iq $PR $OUT/udp_${PR}_rx.ts --rx decdvbt >/dev/null 2>&1; echo "decode exit $?")
  python3 - $OUT/udp_${PR}_rx.ts "$TS" <<'EOF'
import sys
rx = open(sys.argv[1], "rb").read(); orig = open(sys.argv[2], "rb").read()
pk = [rx[i:i + 188] for i in range(0, len(rx) - 187, 188)]
nul = sum(1 for p in pk if ((p[1] & 0x1F) << 8 | p[2]) == 0x1FFF)
data = [p for p in pk if ((p[1] & 0x1F) << 8 | p[2]) != 0x1FFF]
# Nutzpakete müssen als zusammenhängender Ausschnitt des Original-TS (zyklisch) erscheinen
op = [orig[i:i + 188] for i in range(0, len(orig) - 187, 188)]
op = [p for p in op if ((p[1] & 0x1F) << 8 | p[2]) != 0x1FFF]          # Nullpakete des Originals zählen nicht
k0 = None
for k in range(len(op)):                                   # Anfang suchen: 8 aufeinanderfolgende Pakete gleich
    if op[k] == data[0] and all(op[(k + j) % len(op)] == data[j] for j in range(1, 8)):
        k0 = k; break
bad = sum(1 for j, p in enumerate(data) if k0 is None or op[(k0 + j) % len(op)] != p)
print(f"Empfang: {len(pk)} Pakete, davon {nul} Nullpakete (gestopft), {len(data)} Nutzpakete, {bad} nicht in Originalfolge")
EOF
fi
