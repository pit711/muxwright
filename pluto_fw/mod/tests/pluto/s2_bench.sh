#!/bin/bash
# DVB-S2-Durchsatz auf dem Pluto (ohne HF): dvbs2_stage --bench je MODCOD
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
P=$ROOT/pluto_fw/mod/tests/pluto/pl.sh
bash $P kill-stale >/dev/null
bash $P push /opt/dablab/mod/out/dvbs2_mod /opt/dablab/mod/out/dvbs2_stage || exit 1
bash $P run 'cd /tmp; for c in "0 3" "0 6" "0 10" "1 6" "1 8" "1 10" "2 6" "3 9"; do set -- $c; ./dvbs2_stage --bench 3 --mod $1 --rate $2; done'
