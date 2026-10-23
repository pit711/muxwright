#!/bin/bash
# Build-Nachweis DVB-T aus /usr/bin/dvbt_mod: 30 s stumm (−89,75 dB) in den DAC, Unterlauf über dablab-agent,
# Inhaltsprüfung per Eigenempfang (RX-Mitschnitt 1,5 s → gr-dtv → Vergleich mit Quell-TS).
#   bash dvbt_mute_rx.sh [RX-Gain dB]   (WSL dablab)
RXG=${1:-70}
P=192.168.2.1; A=http://$P:8080
SSH="sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@$P"
SC=${OUT:-/tmp/dablab}
K=/mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/mod/tests/k56
echo "## TS auf den Pluto (K36: 8k/16-QAM/2/3/GI 1/4)"
$SSH "[ -f /tmp/k36.ts ] && [ \$(wc -c < /tmp/k36.ts) = 66337680 ] || cat > /tmp/k36.ts" < /opt/dablab/mod/k56/k36.ts
$SSH "cat > /tmp/rx_cap15.sh" < $K/rx_cap15.sh
$SSH "ls -la /tmp/k36.ts; cat /sys/bus/iio/devices/iio:device*/buffer/enable | tr '\n' ' '" 2>&1 | grep -v Warning; echo
echo "## dvbt_mod aus /usr/bin über den Agent, 30 s, ohne --enable (bleibt −89,75 dB)"
curl -s -X POST -d '{"cmd":"/usr/bin/dvbt_mod","args":["-i","/tmp/k36.ts","--loop","-o","iio:","--mode","8k","--const","16qam","--rate","2/3","--guard","1/4","--bw","8","--lo","594000000","--rfbw","8000000","--affinity","--rt","--seconds","30"],"cpu":-1,"rt_prio":0}' $A/api/mod/start; echo
sleep 4; curl -s -X POST $A/api/underflow/clear; echo
sleep 4
echo "## t≈8 s Status"; curl -s $A/api/status | grep -o '"cpu_pct":{[^}]*}\|"dac":{[^}]*}\|"mod":{"running":[a-z]*'
echo "## RX-Mitschnitt 1,5 s auf 594 MHz, RX-Gain $RXG dB"
$SSH "sh /tmp/rx_cap15.sh $RXG" 2>&1 | grep -v Warning
for t in 1 2; do sleep 6; echo "## Status"; curl -s $A/api/status | grep -o '"dac":{"underflows":[0-9]*,"unf_bit":-\?[0-9]*\|"gain_db":[-0-9.]*\|"mod":{"running":[a-z]*'; done
sleep 12
echo "## nach Ende"; curl -s $A/api/status | grep -o '"dac":{"underflows":[0-9]*\|"gain_db":[-0-9.]*\|"exit":-\?[0-9]*'
curl -s $A/api/mod/log | sed 's/\\u000a/\n/g; s/\\"/"/g' | grep -E '"done"|rt_factor|underflow|stuff|error|Fehler' | tail -3
echo "## Mitschnitt holen und decodieren"
$SSH "cat /tmp/rx15.iq" > $SC/rx15.iq; ls -la $SC/rx15.iq
bash $K/rx_decode.sh
