#!/bin/sh
# läuft auf dem Pluto (busybox sh) in /tmp – nur CPU, keine IIO/DAC-Zugriffe
cd /tmp
echo "## loadavg: $(cat /proc/loadavg)  uptime: $(cut -d' ' -f1 /proc/uptime) s"
for v in unfused fused64 fused128 fused32 fusedO2; do
  [ -x ./st_$v ] || continue
  echo "## selftest $v: $(./st_$v | grep -E 'crc|SELFTEST' | tr '\n' ' ')"
  echo "## micro $v"; ./st_$v micro | grep -E "ifft|bit_il|frame|rs_|convcode|sym_il|prbs|convint"
done
echo "## bench 2 threads affinity, 8k 64qam 7/8 GI 1/32, 1088 symbols"
for v in unfused fused64 fused128 fusedO2; do
  [ -x ./mod_$v ] || continue
  echo "## $v"; ./mod_$v --bench 1088 --mode 8k --const 64qam --rate 7/8 --affinity --profile --quiet 2>&1 | tail -1
done
