#!/bin/bash
# Stumme Inhaltsprüfung: dvbt_mod (/usr/bin) → DMA → AD9361 digitaler TX→RX-Loopback (debugfs loopback=1) → RX-Mitschnitt
# → gr-dtv bzw. dvbt_analyze.py → Vergleich mit Quell-TS. TX bleibt −89,75 dB, kein HF-Pegel.
P=192.168.2.1; A=http://$P:8080
SSH="sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@$P"
W=/opt/dablab/fw/rxcheck; mkdir -p $W
T=/opt/dablab/mod/src/tests
$SSH "[ -f /tmp/k36.ts ] && [ \$(wc -c < /tmp/k36.ts) = 66337680 ] || cat > /tmp/k36.ts" < /opt/dablab/mod/k56/k36.ts 2>/dev/null
curl -s -X POST -d '{"cmd":"/usr/bin/dvbt_mod","args":["-i","/tmp/k36.ts","--loop","-o","iio:","--mode","8k","--const","16qam","--rate","2/3","--guard","1/4","--bw","8","--lo","594000000","--rfbw","8000000","--affinity","--rt","--seconds","30"],"cpu":-1,"rt_prio":0}' $A/api/mod/start; echo
sleep 4; curl -s -X POST $A/api/underflow/clear > /dev/null; sleep 3
$SSH 'PHY=$(dirname $(grep -l "^ad9361-phy$" /sys/bus/iio/devices/iio:device*/name)); D=/sys/kernel/debug/iio/$(basename $PHY)
echo "## loopback vorher: $(cat $D/loopback)  gain=$(cat $PHY/out_voltage0_hardwaregain)  tx_path=$(cat $PHY/tx_path_rates)"
echo 1 > $D/loopback; sleep 0.5; echo "## loopback jetzt: $(cat $D/loopback)  rx_path=$(cat $PHY/rx_path_rates)"
iio_readdev -u local: -b 1048576 -s 13714286 cf-ad9361-lpc voltage0 voltage1 > /tmp/rx_lb.iq 2>/dev/null
echo 0 > $D/loopback; echo "## loopback zurück: $(cat $D/loopback), Bytes $(wc -c < /tmp/rx_lb.iq)"' 2>&1 | grep -v Warning
sleep 25
echo "## Modulator-Ende"; curl -s $A/api/status | grep -o '"dac":{"underflows":[0-9]*\|"gain_db":[-0-9.]*\|"exit":-\?[0-9]*'
curl -s $A/api/mod/log | sed 's/\\u000a/\n/g; s/\\"/"/g' | grep '"done"'
$SSH "cat /tmp/rx_lb.iq" > $W/rx_lb.iq
python3 - <<EOF
import numpy as np
r = np.fromfile("$W/rx_lb.iq", np.int16)
print("Samples:", len(r)//2, " max|x|:", np.abs(r).max(), " RMS:", round(float(np.sqrt(np.mean(r.astype(float)**2))),1))
x = np.clip(r.astype(np.int32) * 8, -32768, 32767).astype(np.int16); x.tofile("$W/rx_lb_norm.iq")
EOF
echo "## dvbt_analyze.py (Synchronisation, Piloten, TPS)"
timeout 600 python3 $T/dvbt_analyze.py $W/rx_lb_norm.iq 2>&1 | tail -15
echo "## gr-dtv"
nice -n 10 timeout 900 python3 $T/dvbt_rx_grdtv.py --iq $W/rx_lb_norm.iq --mode 8k --const 16qam --rate 2/3 --guard 1/4 -o $W/rx_lb.ts > $W/grdtv.log 2>&1; echo "rc=$?"
ls -la $W/rx_lb.ts 2>/dev/null
python3 /opt/dablab/mod/tscmp.py $W/rx_lb.ts /opt/dablab/mod/k56/k36.ts 2>&1 | tail -3
tsanalyze $W/rx_lb.ts 2>/dev/null | grep -E "Service name|Transport errors|Discontinu|Bitrate" | head -5
