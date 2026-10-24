#!/bin/bash
# dvbt2_mod auf dem Pluto: Selbsttest, Bitgleichheit ARM vs. x86 (md5 der IQ-Ausgabe, prbs, 2 Rahmen je Profil) und
# Echtzeitfaktor je Profil. NUR CPU: Ausgabe -o /dev/null bzw. md5sum, NIE iio: (der Pluto sendet nicht).
# Vor jedem Lauf: kein anderer *_mod/Benchmark aktiv (sonst warten); ein Wächter prüft während des Laufs jede Sekunde,
# Treffer → "STOERUNG" (Lauf ungültig).   Aufruf aus WSL: tests/pluto/dvbt2_bench.sh [SEKUNDEN] [x86-Binary]
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; ROOT="$(cd $M/../../.. && pwd)"; P=$M/pluto/pl.sh
SECS=${1:-20}; X86=${2:-/opt/dablab/mod/t2/dvbt2_mod}
PAT="_mod|bench|dvbs2_stage|dvbmod_selftest"
wait_free() {
  for i in $(seq 1 90); do
    busy=$(bash $P run "ps | grep -E '$PAT' | grep -v grep")
    [ -z "$busy" ] && return 0
    echo "Pluto belegt ($(echo "$busy" | head -1 | cut -c1-80)), warte 20 s …" >&2; sleep 20
  done
  echo "Pluto dauerhaft belegt – Abbruch" >&2; exit 1
}
run_guarded() {   # $1 = Befehl (ohne iio)
  case "$1" in *iio:*) echo "VERBOTEN: iio-Ausgabe" >&2; exit 1;; esac
  wait_free
  bash $P run "cd /tmp && rm -f t2watch.log; $1 & K=\$!; \
    while kill -0 \$K 2>/dev/null; do ps | grep -E '$PAT' | grep -v grep | grep -v dvbt2_mod_iio >> t2watch.log; sleep 1; done; wait \$K; \
    if [ -s t2watch.log ]; then echo STOERUNG: \$(sort -u t2watch.log | cut -c1-90 | head -3); else echo SAUBER; fi"
}
bash $P kill-stale >/dev/null
head -c 4000000 ${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx/ref/orig_de.ts > /tmp/t2in.ts 2>/dev/null || head -c 4000000 /dev/zero > /tmp/t2in.ts
bash $P push $ROOT/pc/device_files/dvbt2_mod_iio /tmp/t2in.ts >/dev/null || exit 1
run_guarded "./dvbt2_mod_iio --selftest"
for pr in de leicht; do
  x=$($X86 --preset $pr -i prbs --frames 2 -o - --quiet --threads 2 2>/dev/null | md5sum | cut -c1-32)
  a=$(run_guarded "./dvbt2_mod_iio --preset $pr -i prbs --frames 2 -o - --quiet 2>/dev/null | md5sum" | head -1 | cut -c1-32)
  [ "$x" = "$a" ] && echo "md5 $pr: ARM = x86 ($a)" || echo "md5 $pr: ARM $a != x86 $x"
done
for pr in de leicht; do
  echo "== $pr, $SECS s, --affinity --rt"
  run_guarded "cat /proc/loadavg; ./dvbt2_mod_iio --preset $pr -i /tmp/t2in.ts --loop -o /dev/null --seconds $SECS --affinity --rt --profile --quiet $EXTRA 2>&1 | tail -1; cat /proc/loadavg"
done
# weitere Konfigurationen (Eingang TS-Datei in Schleife); CASES leer lassen mit NOCASES=1
CASES=(
 "--preset de -i null"
 "--fft 16k --ext 1 --const 64qam --rate 3/5 --guard 1/8 --pp 2"
 "--fft 16k --ext 1 --const 64qam --rate 2/3 --guard 19/256 --pp 4"
 "--fft 32k --ext 1 --const 256qam --rate 2/3 --guard 1/128 --pp 7"
 "--fft 32k --ext 1 --const 16qam --rate 2/3 --guard 19/256 --pp 4"
 "--fft 8k --ext 1 --const 256qam --rate 3/4 --guard 1/32 --pp 7"
)
[ -n "$NOCASES" ] && exit 0
for a in "${CASES[@]}"; do
  case "$a" in *"-i "*) in="";; *) in="-i /tmp/t2in.ts --loop";; esac
  echo "== $a, $SECS s"
  run_guarded "./dvbt2_mod_iio $a $in -o /dev/null --seconds $SECS --affinity --rt --quiet 2>&1 | tail -1"
done
