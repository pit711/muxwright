#!/bin/bash
# iiod-Durchsatz PC -> Pluto wie Koordinator-Messung: 400 MB Nullen über iio_writedev (Git-Bash unter Windows)
#   bash writedev_rate.sh [libiio-Verzeichnis]   (Standard: pc/libiio = libiio 0.25)
LIB=${1:-/g/DEEPSEEK/plutoTX/dablab/pc/libiio}
curl -s -X POST -d '{"rate_hz":30720000}' http://192.168.2.1:8080/api/rate; echo
curl -s -X POST -d '{"atten_db":89.75}' http://192.168.2.1:8080/api/rf; echo
"$LIB/iio_info.exe" -u ip:192.168.2.1 2>&1 | grep -E "Library version|Backend version|fw_version" | head -4
for uri in usb: ip:192.168.2.1; do
  for b in 1048576 4194304; do
    t0=$(date +%s.%N)
    head -c 400000000 /dev/zero | "$LIB/iio_writedev.exe" -u $uri -b $b -T 0 cf-ad9361-dds-core-lpc voltage0 voltage1 2>&1 | tail -2
    t1=$(date +%s.%N)
    awk -v a=$t0 -v b=$t1 -v u=$uri -v bs=$b 'BEGIN{d=b-a; printf "%-16s -b %-8d %.1f s  %.1f MB/s  %.2f MS/s\n", u, bs, d, 400/d, 100/d}'
  done
done
curl -s http://192.168.2.1:8080/api/status | grep -o '"iiod":{[^}]*}\|"cpu_pct":{[^}]*}'
