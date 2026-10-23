#!/bin/bash
# dvbs2_mod auf dem Pluto ohne HF (Ausgabe /dev/null, prbs): Kodierung + Ausgabekopie, Kern 1 wie im Betrieb
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
P=$ROOT/pluto_fw/mod/tests/pluto/pl.sh
bash $P push /opt/dablab/mod/out/dvbs2_mod >/dev/null || exit 1
bash $P run 'cd /tmp; for c in "qpsk 3/4 1" "qpsk 9/10 1" "8psk 3/4 1" "8psk 9/10 1"; do set -- $c; taskset 2 ./dvbs2_mod -i prbs -o /dev/null --mod $1 --rate $2 --pilots $3 --frames 3000 --quiet 2>&1 | tail -1 | sed "s|^|$1 $2: |"; done'
