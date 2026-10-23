#!/bin/sh
# PAL stoppen → DVB-T K36 8 MHz starten (FIR aus), LO an, 0 dB, Rücklesen, RX-Mitschnitt 594 MHz
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
sh /tmp/pal_ctl.sh stop
rm -f /tmp/k36_7.ts
echo 0 > $PHY/out_voltage_filter_fir_en
sh /tmp/k56_ctl.sh start /tmp/k36.ts 594000000 --backoff 9
echo 0 > $PHY/out_voltage_filter_fir_en 2>/dev/null
echo 0 > $PHY/out_altvoltage1_TX_LO_powerdown
sh /tmp/k56_ctl.sh gain 0
sleep 4
echo "## FIR: out_voltage_filter_fir_en=$(cat $PHY/out_voltage_filter_fir_en) in_out_voltage_filter_fir_en=$(cat $PHY/in_out_voltage_filter_fir_en 2>/dev/null) in_voltage_filter_fir_en=$(cat $PHY/in_voltage_filter_fir_en 2>/dev/null) config: $(cat $PHY/filter_fir_config)"
echo "## tx_path_rates: $(cat $PHY/tx_path_rates)"
echo "## sr=$(cat $PHY/out_voltage_sampling_frequency) rf_bandwidth=$(cat $PHY/out_voltage_rf_bandwidth) LO=$(cat $PHY/out_altvoltage1_TX_LO_frequency) LO_pd=$(cat $PHY/out_altvoltage1_TX_LO_powerdown) gain=$(cat $PHY/out_voltage0_hardwaregain)"
sh /tmp/k56_ctl.sh status
# RX-Mitschnitt
RXLO0=$(cat $PHY/out_altvoltage0_RX_LO_frequency); GM0=$(cat $PHY/in_voltage0_gain_control_mode)
echo 594000000 > $PHY/out_altvoltage0_RX_LO_frequency; echo manual > $PHY/in_voltage0_gain_control_mode; echo 0 > $PHY/in_voltage0_hardwaregain
sleep 1
iio_readdev -u local: -b 1048576 -s 2097152 cf-ad9361-lpc voltage0 voltage1 > /tmp/rx_dvbt.iq 2>/dev/null
echo $RXLO0 > $PHY/out_altvoltage0_RX_LO_frequency; echo $GM0 > $PHY/in_voltage0_gain_control_mode
echo "## rx: $(ls -la /tmp/rx_dvbt.iq | awk '{print $5}') Bytes, RX zurück LO=$(cat $PHY/out_altvoltage0_RX_LO_frequency) mode=$(cat $PHY/in_voltage0_gain_control_mode)"
