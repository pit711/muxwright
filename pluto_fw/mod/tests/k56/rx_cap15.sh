#!/bin/sh
# 1,5 s RX-Mitschnitt auf 594 MHz ohne TX-Ratenänderung; RX-FIR bleibt aus
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
RXLO0=$(cat $PHY/out_altvoltage0_RX_LO_frequency); GM0=$(cat $PHY/in_voltage0_gain_control_mode); RXBW0=$(cat $PHY/in_voltage_rf_bandwidth)
TXR0=$(cat $PHY/tx_path_rates)
echo "## vorher: rx_fir=$(cat $PHY/in_voltage_filter_fir_en) rxbw=$RXBW0 tx_path=$TXR0"
[ "$(cat $PHY/in_voltage_filter_fir_en)" = "0" ] || { echo "RX-FIR ist an – Abbruch (Abschalten würde BBPLL neu programmieren)"; exit 2; }
echo 594000000 > $PHY/out_altvoltage0_RX_LO_frequency
echo 9000000 > $PHY/in_voltage_rf_bandwidth
echo manual > $PHY/in_voltage0_gain_control_mode
echo ${1:-9} > $PHY/in_voltage0_hardwaregain
sleep 1
echo "## Messung: rxbw=$(cat $PHY/in_voltage_rf_bandwidth) rxgain=$(cat $PHY/in_voltage0_hardwaregain) rx_path=$(cat $PHY/rx_path_rates)"
echo "## tx_path unverändert: $( [ "$(cat $PHY/tx_path_rates)" = "$TXR0" ] && echo ja || echo NEIN )"
iio_readdev -u local: -b 1048576 -s 13714286 cf-ad9361-lpc voltage0 voltage1 > /tmp/rx15.iq 2>/dev/null
echo "## Bytes: $(wc -c < /tmp/rx15.iq)"
echo $RXBW0 > $PHY/in_voltage_rf_bandwidth; echo $RXLO0 > $PHY/out_altvoltage0_RX_LO_frequency; echo $GM0 > $PHY/in_voltage0_gain_control_mode
sh /tmp/k56_ctl.sh status | head -1
