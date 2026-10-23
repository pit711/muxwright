#!/bin/bash
# Wie schnell spielt welle-cli eine Datei ab? (Echtzeit = 1 Dateidurchlauf je Dateidauer)
F=${1:-/tmp/r.cf32.iq}; T=${2:-40}
D=$(python3 -c "import os;print(round(os.path.getsize('$F')/8/2048000,2))")
cd /tmp; welle-cli -D -w 7123 -f $F > /tmp/ws.log 2>&1 & P=$!
sleep $T; kill $P; wait $P 2>/dev/null
N=$(grep -c "End of file, restarting" /tmp/ws.log)
echo "Datei ${D} s, ${T} s Wanduhr, Neustarts: $N  (Echtzeit waeren $(python3 -c "print(int($T/$D))"))"
