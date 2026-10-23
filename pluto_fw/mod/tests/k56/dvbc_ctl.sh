#!/bin/sh
# DVB-C-Sender auf dem Pluto: start [TS] [LO] [dvbc_mod-Optionen] | gain DB | status | stop
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
DDS=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name))
DBG=/sys/kernel/debug/iio/$(basename $DDS)/direct_reg_access
unf_read()  { echo 0x80000088 > $DBG 2>/dev/null; v=$(cat $DBG 2>/dev/null); echo $(( ${v:-0} & 1 )); }
case "$1" in
start)
  [ "$(cat $DDS/buffer/enable)" = "0" ] || { echo "ABBRUCH: DAC belegt"; exit 2; }
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  TS=${2:-/tmp/kabel.ts}; LO=${3:-546000000}; shift 3 2>/dev/null
  nohup ${BIN:-/usr/bin/dvbc_mod} -i $TS --loop -o iio: --lo $LO "$@" > /dev/null 2> /tmp/dvbc.log &
  echo $! > /tmp/dvbc.pid
  sleep 4; echo 0x80000088 0x1 > $DBG 2>/dev/null
  echo "gestartet pid $(cat /tmp/dvbc.pid)"
  ;;
gain)
  awk -v g="$2" 'BEGIN{ if (g > 0 || g < -89.75) exit 1 }' || { echo "ABGELEHNT: $2"; exit 2; }
  echo "$2" > $PHY/out_voltage0_hardwaregain; echo "hardwaregain jetzt $(cat $PHY/out_voltage0_hardwaregain)" ;;
status)
  P=$(cat /tmp/dvbc.pid 2>/dev/null); if [ -n "$P" ] && kill -0 $P 2>/dev/null; then run=läuft; else run=GESTOPPT; fi
  echo "dvbc: $run unf=$(unf_read) gain=$(cat $PHY/out_voltage0_hardwaregain) LO=$(cat $PHY/out_altvoltage1_TX_LO_frequency) LO_pd=$(cat $PHY/out_altvoltage1_TX_LO_powerdown) sr=$(cat $PHY/out_voltage_sampling_frequency) rfbw=$(cat $PHY/out_voltage_rf_bandwidth) fir_en=$(cat $PHY/out_voltage_filter_fir_en)"
  echo "fir: $(cat $PHY/filter_fir_config)  tx_path: $(cat $PHY/tx_path_rates)"
  grep '^{' /tmp/dvbc.log | tail -1 | cut -c1-220 ;;
stop)
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  P=$(cat /tmp/dvbc.pid 2>/dev/null); [ -n "$P" ] && kill -TERM $P 2>/dev/null; sleep 2; [ -n "$P" ] && kill -0 $P 2>/dev/null && kill -KILL $P
  echo 0 > $PHY/out_voltage_filter_fir_en; echo 1 > $PHY/out_altvoltage1_TX_LO_powerdown
  echo "gestoppt: gain=$(cat $PHY/out_voltage0_hardwaregain) buf_en=$(cat $DDS/buffer/enable) fir_en=$(cat $PHY/out_voltage_filter_fir_en)" ;;
esac
