#!/bin/bash
# Build-4-Prüfung NACH dem RAM-Boot (boot_ram.cmd out\pluto.dfu) – WSL dablab. Alles stumm (−89,75 dB), kein HF.
#   bash build4_ramtest.sh
# Danach unter Windows: python pluto_fw\test\dab_stream_60s.py G:\DEEPSEEK\plutoTX\dablab (Server vorher starten, danach beenden)
D=/mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/test
P=192.168.2.1; A=http://$P:8080
SSH="sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@$P"
PAL=/opt/dablab/fw/paltest/pal_test.iq
cd $D
echo "######## basic / agent"; bash device_tests.sh basic; bash device_tests.sh agent
echo "######## Agent: Upload / Dateiliste"
curl -s -X POST --data-binary @$PAL "$A/api/upload?name=pal_test.iq"; echo
curl -s $A/api/files; echo
$SSH "md5sum /tmp/dablab_files/pal_test.iq" 2>/dev/null; md5sum $PAL
echo "######## PAL zyklisch über cmd:pal, stumm, 20 s, Loopback-Mitschnitt"
curl -s -X POST -d '{"cmd":"pal","file":"pal_test.iq","lo_hz":593500000,"seconds":20}' $A/api/mod/start; echo
sleep 4; curl -s -X POST $A/api/underflow/clear > /dev/null; sleep 2
curl -s $A/api/status | grep -o '"dac":{[^}]*}\|"mod":{"running":[a-z]*,"pid":[0-9]*,"cmd":"[^"]*"'
$SSH 'PHY=$(dirname $(grep -l "^ad9361-phy$" /sys/bus/iio/devices/iio:device*/name)); DD=/sys/kernel/debug/iio/$(basename $PHY)
echo "tx_path=$(cat $PHY/tx_path_rates) fir_en=$(cat $PHY/in_out_voltage_filter_fir_en) gain=$(cat $PHY/out_voltage0_hardwaregain)"
echo 1 > $DD/loopback; sleep 0.3; iio_readdev -u local: -b 1048576 -s 2560000 cf-ad9361-lpc voltage0 voltage1 > /tmp/pal_lb.iq 2>/dev/null; echo 0 > $DD/loopback
echo "loopback zurück: $(cat $DD/loopback), $(wc -c < /tmp/pal_lb.iq) Byte"' 2>/dev/null
sleep 16
curl -s $A/api/status | grep -o '"dac":{"underflows":[0-9]*\|"exit":-\?[0-9]*\|"gain_db":[-0-9.]*'
curl -s $A/api/mod/log | sed 's/\\u000a/\n/g' | grep -E "ENDE|zyklisch|Fehler|TX-Rate"
$SSH "cat /tmp/pal_lb.iq" > /opt/dablab/fw/paltest/pal_lb.iq 2>/dev/null
python3 - <<'EOF'
import numpy as np
r = np.fromfile("/opt/dablab/fw/paltest/pal_lb.iq", np.int16).astype(np.int32)
x = np.clip(r * 16, -32768, 32767).astype(np.int16); x.tofile("/opt/dablab/fw/paltest/pal_lb_norm.iq")
print("Loopback-Samples", len(r) // 2, "max", int(np.abs(r).max()))
EOF
python3 /mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/mod/tools/pal_check.py --iq /opt/dablab/fw/paltest/pal_lb_norm.iq --png $D/results/build4_pal_loopback.png 2>&1 | tail -6
echo "(Referenz Datei: blank_rel 0,72, white_rel 0,09; Bild: results/build4_pal_loopback.png vs. /opt/dablab/fw/paltest/pal_test_file.png)"
curl -s -X POST -d '{"name":"pal_test.iq"}' $A/api/files/delete; echo
echo "######## DVB-T stumm + Loopback-Inhaltsprüfung"
bash dvbt_loopback_check.sh | grep -E "loopback|underflows|exit|done|Service name|rc="
python3 ts_align_compare.py /opt/dablab/fw/rxcheck/rx_lb.ts /opt/dablab/mod/k56/k36.ts
curl -s $A/api/status | grep -o '"clipped":[0-9]*,"rt_factor":[0-9.]*'
echo "######## fw_printenv-Abgleich"
$SSH fw_printenv 2>/dev/null | sort > /tmp/env_b4; sort $D/results/fw_printenv_after_flash.txt > /tmp/env_ref; diff /tmp/env_ref /tmp/env_b4 && echo "U-Boot-Env unverändert"
