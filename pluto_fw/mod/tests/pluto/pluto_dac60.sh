#!/bin/sh
# Stummer DAC-Test (Pluto, busybox): dvbt_mod → libiio lokal → cf-ad9361-dds-core-lpc, hardwaregain −89,75 dB.
# Unterlauf: FPGA-Register up_status_unf (DAC-Common 0x22 → Byte 0x88 relativ zur DDS-Basis 0x79024000, W1C) über debugfs direct_reg_access.
# Aufruf: sh pluto_dac60.sh BIN SEKUNDEN [weitere dvbt_mod-Optionen]
BIN=${1:-/tmp/dvbt_mod_iio}; SECS=${2:-60}; shift 2; EXTRA="$*"
PHY=$(dirname $(grep -l '^ad9361-phy$' /sys/bus/iio/devices/iio:device*/name))
DDS=$(dirname $(grep -l '^cf-ad9361-dds-core-lpc$' /sys/bus/iio/devices/iio:device*/name))
DBG=/sys/kernel/debug/iio/$(basename $DDS)/direct_reg_access
unf_read()  { echo 0x80000088 > $DBG; v=$(cat $DBG); echo $(( v & 1 )); }
unf_clear() { echo 0x80000088 0x1 > $DBG; }
echo "## firmware: $(grep VERSION= /etc/os-release) uptime $(cut -d' ' -f1 /proc/uptime)"
if [ "$(cat $DDS/buffer/enable)" != "0" ]; then echo "ABBRUCH: DAC-Puffer belegt (buffer/enable=1)"; exit 2; fi
if pgrep -f iqplay >/dev/null 2>&1 || ps | grep -v grep | grep -q iqplay; then echo "ABBRUCH: iqplay läuft"; exit 2; fi
# Zustand sichern
SR0=$(cat $PHY/out_voltage_sampling_frequency); BW0=$(cat $PHY/out_voltage_rf_bandwidth); FIR0=$(cat $PHY/out_voltage_filter_fir_en)
GAIN0=$(cat $PHY/out_voltage0_hardwaregain | cut -d' ' -f1)
echo "## vorher: sampling_frequency=$SR0 rf_bandwidth=$BW0 fir_en=$FIR0 hardwaregain=$GAIN0"
echo -89.75 > $PHY/out_voltage0_hardwaregain
echo "## hardwaregain jetzt: $(cat $PHY/out_voltage0_hardwaregain)"
$BIN -o iio: --seconds $SECS --mode 8k --const 64qam --rate 7/8 --guard 1/32 --affinity --rt --quiet $EXTRA 2>/tmp/dac.log &
PID=$!
sleep 3
echo "## tx_path_rates: $(cat $PHY/tx_path_rates)"
echo "## sampling_frequency: $(cat $PHY/out_voltage_sampling_frequency)  hardwaregain: $(cat $PHY/out_voltage0_hardwaregain)  buf_en=$(cat $DDS/buffer/enable)"
echo "## unf vor dem Löschen (Anlauf): $(unf_read)"; unf_clear; echo "## unf nach dem Löschen: $(unf_read)"
grep -E "^cpu[01] " /proc/stat > /tmp/s0
t=0
while kill -0 $PID 2>/dev/null; do
  sleep 10; t=$((t+10))
  kill -0 $PID 2>/dev/null && echo "## t=+${t}s unf=$(unf_read) gain=$(cat $PHY/out_voltage0_hardwaregain | cut -d' ' -f1) sr=$(cat $PHY/out_voltage_sampling_frequency) rates=$(cat $PHY/tx_path_rates | cut -d' ' -f2) fremd=$(ps | grep -v grep | grep -c -E 'iqplay|iio_writedev|iio_attr')"
done
wait $PID; RC=$?
grep -E "^cpu[01] " /proc/stat > /tmp/s1
echo "## ende rc=$RC"
grep -v '^{"t"' /tmp/dac.log | head -5
tail -1 /tmp/dac.log
paste /tmp/s0 /tmp/s1 | awk '{ b0=$2+$3+$4+$7+$8; i0=$5+$6; b1=$13+$14+$15+$18+$19; i1=$16+$17; d=(b1-b0)+(i1-i0); printf "%s busy=%.1f%%\n", $1, 100*(b1-b0)/d }'
# Zustand wiederherstellen (Dämpfung bleibt −89,75)
echo -89.75 > $PHY/out_voltage0_hardwaregain
echo $FIR0 > $PHY/out_voltage_filter_fir_en 2>/dev/null
echo $SR0 > $PHY/out_voltage_sampling_frequency 2>/dev/null
echo $BW0 > $PHY/out_voltage_rf_bandwidth 2>/dev/null
echo "## nachher: sampling_frequency=$(cat $PHY/out_voltage_sampling_frequency) rf_bandwidth=$(cat $PHY/out_voltage_rf_bandwidth) fir_en=$(cat $PHY/out_voltage_filter_fir_en) hardwaregain=$(cat $PHY/out_voltage0_hardwaregain) buf_en=$(cat $DDS/buffer/enable)"
