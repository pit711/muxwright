#!/bin/bash
# Varianten-Messung dvbt2_mod auf dem Pluto (nur CPU, -o /dev/null, NIE iio:): baut je Variante ein ARM-Binary mit
# zusätzlichen Compiler-Defines, prüft md5 (2 Rahmen DE) gegen x86 und misst den Echtzeitfaktor.
#   tests/pluto/dvbt2_variants.sh SEK PRESET "NAME:-DFOO=1 -DBAR=2" ["NAME2:..."] …
M="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; P=$M/pluto/pl.sh
SECS=$1; PR=$2; shift 2
PAT="_mod|bench|dvbs2_stage|dvbmod_selftest|t2v_"
X=$(/opt/dablab/mod/t2/dvbt2_mod --preset de -i prbs --frames 2 -o - --quiet 2>/dev/null | md5sum | cut -c1-32)
head -c 4000000 ${DVBT2_WORK:-/tmp/dvbt2}/dvbt2_rx/ref/orig_de.ts > /tmp/t2in.ts
L=""
for v in "$@"; do
  n=${v%%:*}; d=${v#*:}
  arm-linux-gnueabihf-gcc -O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -std=gnu11 -D_GNU_SOURCE $d -static -o /tmp/t2v_$n \
     $M/../dvbt2_mod.c $M/../lib/dvbt2.c $M/../lib/dvbt2_fft.c $M/../lib/fft16.c $M/../lib/ts_input.c $M/../lib/output.c $M/../lib/util.c -lm -lpthread || exit 1
  L="$L /tmp/t2v_$n"
done
bash $P kill-stale >/dev/null; bash $P push $L /tmp/t2in.ts >/dev/null || exit 1
for v in "$@"; do
  n=${v%%:*}
  for i in $(seq 1 60); do busy=$(bash $P run "ps | grep -E '$PAT' | grep -v grep"); [ -z "$busy" ] && break; sleep 20; done
  [ -n "$busy" ] && { echo "Pluto belegt – Abbruch"; exit 1; }
  bash $P run "cd /tmp; m=\$(./t2v_$n --preset de -i prbs --frames 2 -o - --quiet 2>/dev/null | md5sum | cut -c1-32); [ \$m = $X ] && echo \"$n md5 ok\" || echo \"$n md5 FALSCH\"; \
     ./t2v_$n --preset $PR -i t2in.ts --loop -o /dev/null --seconds $SECS --affinity --rt --profile --quiet 2>&1 | tail -1 | sed 's/^/$n /'"
done
