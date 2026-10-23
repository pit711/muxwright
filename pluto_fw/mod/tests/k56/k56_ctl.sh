#!/bin/sh
# Steuerung des DVB-T-Senders K56 auf dem Pluto: start | gain DB | status | stop
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
DDS=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name))
DBG=/sys/kernel/debug/iio/$(basename $DDS)/direct_reg_access
unf_read()  { echo 0x80000088 > $DBG 2>/dev/null; v=$(cat $DBG 2>/dev/null); echo $(( ${v:-0} & 1 )); }
unf_clear() { echo 0x80000088 0x1 > $DBG 2>/dev/null; }
case "$1" in
start)
  [ "$(cat $DDS/buffer/enable)" = "0" ] || { echo "ABBRUCH: DAC belegt"; exit 2; }
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  cat $PHY/out_voltage_sampling_frequency $PHY/out_voltage_rf_bandwidth $PHY/out_voltage_filter_fir_en > /tmp/k56_saved
  TS=${2:-/tmp/k56.ts}; LO=${3:-754000000}; shift 3 2>/dev/null; EXTRA="$*"
  nohup /tmp/dvbt_mod_iio -i $TS --loop -o iio: --lo $LO --mode 8k --const 16qam --rate 2/3 --guard 1/4 \
      --window 32 --affinity --rt $EXTRA > /dev/null 2> /tmp/k56.log &
  echo $! > /tmp/k56.pid
  sleep 4; unf_clear
  echo "gestartet pid $(cat /tmp/k56.pid), hardwaregain $(cat $PHY/out_voltage0_hardwaregain), LO $(cat $PHY/out_altvoltage1_TX_LO_frequency), sr $(cat $PHY/out_voltage_sampling_frequency), rfbw $(cat $PHY/out_voltage_rf_bandwidth)"
  ;;
gain)
  G=$2
  # Sicherheitsgrenzen: 0 … −89,75 dB
  awk -v g="$G" 'BEGIN{ if (g > 0 || g < -89.75) exit 1 }' || { echo "ABGELEHNT: $G außerhalb 0…−89,75"; exit 2; }
  echo "$G" > $PHY/out_voltage0_hardwaregain
  echo "hardwaregain jetzt $(cat $PHY/out_voltage0_hardwaregain)"
  ;;
status)
  P=$(cat /tmp/k56.pid 2>/dev/null)
  if [ -n "$P" ] && kill -0 $P 2>/dev/null; then run=läuft; else run=GESTOPPT; fi
  echo "sender: $run  unf=$(unf_read)  hardwaregain=$(cat $PHY/out_voltage0_hardwaregain)  LO=$(cat $PHY/out_altvoltage1_TX_LO_frequency)  sr=$(cat $PHY/out_voltage_sampling_frequency)  temp=$(cat /sys/bus/iio/devices/iio:device2/in_temp0_input 2>/dev/null)"
  grep '^{"t"' /tmp/k56.log | tail -1 | cut -c1-230
  head -1 /proc/loadavg; top -b -n1 | grep -E "^CPU|dvbt_mod" | head -2
  ;;
stop)
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  P=$(cat /tmp/k56.pid 2>/dev/null); [ -n "$P" ] && kill -TERM $P 2>/dev/null; sleep 2
  [ -n "$P" ] && kill -0 $P 2>/dev/null && kill -KILL $P
  tail -1 /tmp/k56.log
  if [ -f /tmp/k56_saved ]; then set -- $(cat /tmp/k56_saved); echo $3 > $PHY/out_voltage_filter_fir_en; echo $1 > $PHY/out_voltage_sampling_frequency; echo $2 > $PHY/out_voltage_rf_bandwidth; fi
  echo "gestoppt: hardwaregain $(cat $PHY/out_voltage0_hardwaregain) buf_en=$(cat $DDS/buffer/enable) sr=$(cat $PHY/out_voltage_sampling_frequency)"
  ;;
esac
