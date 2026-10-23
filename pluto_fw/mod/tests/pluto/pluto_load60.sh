#!/bin/sh
# 60-s-Dauerläufe nach /dev/null mit Kernlast aus /proc/stat (Pluto, busybox). Fremdlast (pluto_dvb) läuft parallel!
cd /tmp
run() {
  echo "## dauerlauf: $*"
  echo "## loadavg vorher: $(cat /proc/loadavg)"
  grep -E "^cpu[01] " /proc/stat > /tmp/stat0
  ./mod_unfused -o /dev/null --seconds 60 "$@" --affinity --rt --quiet 2>/tmp/run.log
  grep -E "^cpu[01] " /proc/stat > /tmp/stat1
  tail -1 /tmp/run.log
  paste /tmp/stat0 /tmp/stat1 | awk '{ busy0=$2+$3+$4+$7+$8; idle0=$5+$6; busy1=$13+$14+$15+$18+$19; idle1=$16+$17; d=(busy1-busy0)+(idle1-idle0); printf "%s busy_total=%.1f%% (inkl. Fremdlast)\n", $1, 100*(busy1-busy0)/d }'
}
run --mode 8k --const 64qam --rate 7/8 --guard 1/32
run --mode 8k --const 64qam --rate 7/8 --guard 1/4
run --mode 2k --const 64qam --rate 7/8 --guard 1/32
echo "## fremdlast danach:"; top -b -n1 2>/dev/null | head -8 | tail -4
