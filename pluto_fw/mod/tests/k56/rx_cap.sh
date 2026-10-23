#!/bin/sh
# RX-Mitschnitt ohne TX-Ratenänderung: rx_cap.sh LO_HZ SAMPLES GAIN_DB RXBW_HZ DATEI
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
RXLO0=$(cat $PHY/out_altvoltage0_RX_LO_frequency); GM0=$(cat $PHY/in_voltage0_gain_control_mode); RXBW0=$(cat $PHY/in_voltage_rf_bandwidth)
TXR0=$(cat $PHY/tx_path_rates)
echo $1 > $PHY/out_altvoltage0_RX_LO_frequency
echo ${4:-8000000} > $PHY/in_voltage_rf_bandwidth
echo manual > $PHY/in_voltage0_gain_control_mode; echo ${3:-9} > $PHY/in_voltage0_hardwaregain
sleep 1
echo "## rx: LO=$(cat $PHY/out_altvoltage0_RX_LO_frequency) rate=$(cat $PHY/in_voltage_sampling_frequency) bw=$(cat $PHY/in_voltage_rf_bandwidth) gain=$(cat $PHY/in_voltage0_hardwaregain) rx_fir=$(cat $PHY/in_voltage_filter_fir_en) tx_path_unverändert=$( [ "$(cat $PHY/tx_path_rates)" = "$TXR0" ] && echo ja || echo NEIN )"
iio_readdev -u local: -b 1048576 -s $2 cf-ad9361-lpc voltage0 voltage1 > ${5:-/tmp/rx.iq} 2>/dev/null
echo "## Bytes: $(wc -c < ${5:-/tmp/rx.iq})"
echo $RXBW0 > $PHY/in_voltage_rf_bandwidth; echo $RXLO0 > $PHY/out_altvoltage0_RX_LO_frequency; echo $GM0 > $PHY/in_voltage0_gain_control_mode
