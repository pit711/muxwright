#!/bin/sh
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
RXLO0=$(cat $PHY/out_altvoltage0_RX_LO_frequency); GM0=$(cat $PHY/in_voltage0_gain_control_mode)
RXBW0=$(cat $PHY/in_voltage_rf_bandwidth); RXFIR0=$(cat $PHY/in_voltage_filter_fir_en)
echo "## RX vorher: bw=$RXBW0 fir_en=$RXFIR0 rx_path=$(cat $PHY/rx_path_rates)"
echo 0 > $PHY/in_voltage_filter_fir_en
echo 9000000 > $PHY/in_voltage_rf_bandwidth
echo 594000000 > $PHY/out_altvoltage0_RX_LO_frequency; echo manual > $PHY/in_voltage0_gain_control_mode; echo 0 > $PHY/in_voltage0_hardwaregain
sleep 1
echo "## RX Messung: bw=$(cat $PHY/in_voltage_rf_bandwidth) fir_en=$(cat $PHY/in_voltage_filter_fir_en) tx_fir=$(cat $PHY/out_voltage_filter_fir_en) rx_path=$(cat $PHY/rx_path_rates)"
iio_readdev -u local: -b 1048576 -s 2097152 cf-ad9361-lpc voltage0 voltage1 > /tmp/rx_dvbt2.iq 2>/dev/null
echo $RXBW0 > $PHY/in_voltage_rf_bandwidth; echo $RXFIR0 > $PHY/in_voltage_filter_fir_en 2>/dev/null
echo $RXLO0 > $PHY/out_altvoltage0_RX_LO_frequency; echo $GM0 > $PHY/in_voltage0_gain_control_mode
echo "## RX zurück: bw=$(cat $PHY/in_voltage_rf_bandwidth) fir_en=$(cat $PHY/in_voltage_filter_fir_en) tx_fir=$(cat $PHY/out_voltage_filter_fir_en) LO=$(cat $PHY/out_altvoltage0_RX_LO_frequency)"
sh /tmp/k56_ctl.sh status | head -1
