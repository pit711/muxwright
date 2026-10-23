#!/bin/bash
# Einzelstufen messen: ffmpeg-Quelle, audioenc, dabmux (Datei), Python-Relais
cd "$(dirname "$0")/.."
W=$(mktemp -d /tmp/perfst.XXXX)
t(){ local s=$(date +%s.%N); "$@"; local e=$(date +%s.%N); python3 -c "print('%-40s %.2f s'%('$1 $2',$e-$s))"; }
echo "30 s Signal je Stufe:"
t ffmpeg -nostdin -loglevel error -f lavfi -i "aevalsrc=exprs='0.1*sin(2*PI*1000*t)|0.1*sin(2*PI*1000*t)':s=48000" -t 30 -f s16le -y $W/a.raw
t ffmpeg -nostdin -loglevel error -f lavfi -i "sine=f=1000:r=48000" -ac 2 -t 30 -f s16le -y $W/b.raw
t odr-audioenc -f raw -i $W/a.raw -b 96 --aaclc -o $W/a.dabp
t odr-audioenc -f raw -i $W/a.raw -b 88 --ps -o $W/p.dabp
cat > $W/m.mux <<M
general { dabmode 1 nbframes 1250 }
remotecontrol { telnetport 0 }
ensemble { id 0x4FFF ecc 0xE0 label "P" shortlabel "P" }
services { s { id 0xD001 label "P" } }
subchannels { a { type dabplus bitrate 96 id 1 protection 3 inputproto file inputuri "$W/a.dabp" } }
components { c { service s subchannel a } }
outputs { o "file://$W/m.eti?type=raw" }
M
t odr-dabmux $W/m.mux
ls -la $W/m.eti | awk '{print "eti bytes", $5}'
rm -rf $W
