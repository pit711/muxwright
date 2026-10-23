#!/bin/sh
# 60 s getakteter Lauf (--realtime, nominale 9,142857 MS/s) nach /dev/null, Kernlast aus /proc/stat
cd /tmp
echo "## getaktet 60 s: 8k 64qam 7/8 1/32, --affinity --rt --realtime"
grep -E "^cpu[01] " /proc/stat > /tmp/stat0
./mod_unfused -o /dev/null --seconds 60 --mode 8k --const 64qam --rate 7/8 --guard 1/32 --affinity --rt --realtime 2>/tmp/run.log
grep -E "^cpu[01] " /proc/stat > /tmp/stat1
grep -E '"t":(10|30|59)\.' /tmp/run.log | cut -c1-200
tail -1 /tmp/run.log
paste /tmp/stat0 /tmp/stat1 | awk '{ busy0=$2+$3+$4+$7+$8; idle0=$5+$6; busy1=$13+$14+$15+$18+$19; idle1=$16+$17; d=(busy1-busy0)+(idle1-idle0); printf "%s busy_total=%.1f%% (inkl. Fremdlast pluto_dvb ~50%%)\n", $1, 100*(busy1-busy0)/d }'
