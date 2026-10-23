#!/bin/sh
# Analog-TV PAL B/G zyklisch über iio_writedev -c (lokal): start [DATEI] [LO] | gain DB | status | stop
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
DDS=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name))
case "$1" in
start)
  F=${2:-/tmp/pal_k36.iq}; LO=${3:-593500000}
  [ "$(cat $DDS/buffer/enable)" = "0" ] || { echo "ABBRUCH: DAC belegt"; exit 2; }
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  cat $PHY/out_voltage_sampling_frequency $PHY/out_voltage_rf_bandwidth $PHY/out_voltage_filter_fir_en > /tmp/pal_saved
  echo 0 > $PHY/out_voltage_filter_fir_en
  echo 8000000 > $PHY/out_voltage_sampling_frequency
  echo 8000000 > $PHY/out_voltage_rf_bandwidth
  echo $LO > $PHY/out_altvoltage1_TX_LO_frequency
  echo 0 > $PHY/out_altvoltage1_TX_LO_powerdown
  N=$(( $(wc -c < $F) / 4 ))
  nohup sh -c "iio_writedev -u local: -c -b $N cf-ad9361-dds-core-lpc voltage0 voltage1 < $F" > /tmp/pal.log 2>&1 &
  echo $! > /tmp/pal.pid
  sleep 3
  echo "gestartet: Samples/Schleife $N, buf_en=$(cat $DDS/buffer/enable), sr=$(cat $PHY/out_voltage_sampling_frequency), rfbw=$(cat $PHY/out_voltage_rf_bandwidth), fir_en=$(cat $PHY/out_voltage_filter_fir_en), LO=$(cat $PHY/out_altvoltage1_TX_LO_frequency), gain=$(cat $PHY/out_voltage0_hardwaregain)"
  ;;
gain)
  awk -v g="$2" 'BEGIN{ if (g > 0 || g < -89.75) exit 1 }' || { echo "ABGELEHNT: $2"; exit 2; }
  echo "$2" > $PHY/out_voltage0_hardwaregain; echo "hardwaregain jetzt $(cat $PHY/out_voltage0_hardwaregain)"
  ;;
status)
  echo "buf_en=$(cat $DDS/buffer/enable) iio_writedev=$(ps | grep -v grep | grep -c 'iio_writedev') sr=$(cat $PHY/out_voltage_sampling_frequency) rfbw=$(cat $PHY/out_voltage_rf_bandwidth) fir_en=$(cat $PHY/out_voltage_filter_fir_en) LO=$(cat $PHY/out_altvoltage1_TX_LO_frequency) LO_pd=$(cat $PHY/out_altvoltage1_TX_LO_powerdown) gain=$(cat $PHY/out_voltage0_hardwaregain)"
  tail -2 /tmp/pal.log 2>/dev/null
  ;;
stop)
  echo -89.75 > $PHY/out_voltage0_hardwaregain
  for p in $(ps | grep -v grep | grep 'iio_writedev' | awk '{print $1}'); do kill -TERM $p 2>/dev/null; done
  sleep 2
  if [ -f /tmp/pal_saved ]; then set -- $(cat /tmp/pal_saved); echo $1 > $PHY/out_voltage_sampling_frequency; echo $2 > $PHY/out_voltage_rf_bandwidth; fi
  echo "gestoppt: gain=$(cat $PHY/out_voltage0_hardwaregain) buf_en=$(cat $DDS/buffer/enable)"
  ;;
esac
