#!/bin/bash
cd /tmp
ffmpeg -nostdin -loglevel error -f lavfi -i "sine=f=1000:r=48000" -ac 2 -t 10 -f s16le -y /tmp/x.raw
for opt in "--aaclc" "--aaclc -A" "--sbr" "--ps" "-a"; do
  s=$(date +%s.%N); odr-audioenc -f raw -i /tmp/x.raw -b 96 $opt -o /tmp/x.out >/dev/null 2>&1; e=$(date +%s.%N)
  echo "$opt: 10 s Audio in $(python3 -c "print(round($e-$s,2))") s"
done
ldd /opt/dablab/bin/odr-audioenc | grep -i -E "fdk|toolame|vlc|zmq|curl"
grep -n "fdk" /opt/dablab/src/ODR-AudioEnc/config.log | head -5
ls /opt/dablab/src/ODR-AudioEnc/
