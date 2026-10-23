#!/bin/sh
# 20 s stummer DAC-Lauf mit Statuszeilen und top-Schnappschuss (Ursachenanalyse)
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
DDS=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name))
[ "$(cat $DDS/buffer/enable)" = "0" ] || { echo "ABBRUCH: DAC belegt"; exit 2; }
SR0=$(cat $PHY/out_voltage_sampling_frequency); BW0=$(cat $PHY/out_voltage_rf_bandwidth); FIR0=$(cat $PHY/out_voltage_filter_fir_en)
echo -89.75 > $PHY/out_voltage0_hardwaregain
/tmp/dvbt_mod_iio -o iio: --seconds 20 --mode 8k --const 64qam --rate 7/8 --guard 1/32 --affinity --rt $* 2>/tmp/dd.log &
PID=$!
sleep 8; top -b -n1 | head -16; sleep 1
wait $PID
grep -E '"t":(2|5|10|15|19)\.' /tmp/dd.log | cut -c1-200; tail -1 /tmp/dd.log
echo -89.75 > $PHY/out_voltage0_hardwaregain; echo $FIR0 > $PHY/out_voltage_filter_fir_en; echo $SR0 > $PHY/out_voltage_sampling_frequency; echo $BW0 > $PHY/out_voltage_rf_bandwidth
echo "restored: sr=$(cat $PHY/out_voltage_sampling_frequency) gain=$(cat $PHY/out_voltage0_hardwaregain) buf_en=$(cat $DDS/buffer/enable)"
