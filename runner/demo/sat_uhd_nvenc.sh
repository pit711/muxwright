#!/bin/bash
# DVB-S-Live-Demo: Big Buck Bunny 2160p komplett, endlos, NVENC HEVC 2160p25 ~16 Mbit/s als SID 2 "DABLAB Sat UHD"
# fuer den DVB-S-Transponder (22 MBd, FEC 7/8, Nutzrate 35 480 392 b/s, ONID 0xFF03, TSID 3) -> UDP an den Pluto-Modulator.
#
#   bash sat_uhd_nvenc.sh start [ZIEL] [CONFIG]   Standard: 192.168.2.1:1234, examples/dvbs_uhd_nvenc.json (nur SID 2)
#   bash sat_uhd_nvenc.sh start3 [ZIEL]           alle drei Dienste live (SID 1 HD H.264, SID 2 UHD, SID 3 HDR10) per NVENC
#   bash sat_uhd_nvenc.sh dry [SEK]               Probelauf an 127.0.0.1:12345 (nc -lu), misst die Rate - sendet NICHT zum Pluto
#   bash sat_uhd_nvenc.sh stop | status | ctl '<json>'
#
# Aufruf aus Windows: wsl -d dablab -u root -- bash /mnt/g/DEEPSEEK/plutoTX/dablab/runner/demo/sat_uhd_nvenc.sh start
# Vor dem Umschalten den bisherigen Strom (stream_sat_3.sh stop) beenden: nur EIN Sender auf 192.168.2.1:1234.
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=/mnt/g/DEEPSEEK/plutoTX/dablab/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=/mnt/g/DEEPSEEK/plutoTX/dablab/runner
STATE=/tmp/dablab_sat_uhd
mkdir -p $STATE
cd $R
mkcfg() {   # $1 Vorlage, $2 host:port
  python3 - "$1" "$2" "$STATE/config.json" <<'P'
import json, sys
src, dst, out = sys.argv[1:4]
c = json.load(open(src, encoding="utf-8"))
h, p = dst.rsplit(":", 1)
c["output"] = {"kind": "udp", "host": h, "port": int(p), "burst": 7}
json.dump(c, open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
P
}
start() {   # $1 Ziel, $2 Vorlage
  if [ -f $STATE/pid ] && kill -0 $(cat $STATE/pid) 2>/dev/null; then echo "laeuft schon (PID $(cat $STATE/pid))"; exit 1; fi
  mkcfg "$2" "$1"
  python3 dablab_run.py validate --config $STATE/config.json | python3 -c "
import sys,json; r=json.load(sys.stdin); s=r['summary']
print('Konfiguration ok=%s  %s  Nutzrate %.1f b/s  Budget %s' % (r['ok'], s['modulation'], s['useful_bitrate_bps'], s['budget']))
for x in s['services']: print('  SID %s %-16s %s' % (x['service_id'], x['name'], x['video']))
sys.exit(0 if r['ok'] else 1)" || exit 1
  setsid nohup nice -n 10 python3 dablab_run.py live --config $STATE/config.json > /dev/null 2> $STATE/live.log &
  echo $! > $STATE/pid
  sleep 6
  head -1 $STATE/live.log
  grep -E '"lvl": "error"' $STATE/live.log | head -3
  echo "gestartet: PID $(cat $STATE/pid) -> udp://$1 (Log $STATE/live.log)"
}
case "$1" in
start)  start "${2:-192.168.2.1:1234}" "${3:-examples/dvbs_uhd_nvenc.json}" ;;
start3) start "${2:-192.168.2.1:1234}" examples/dvbs_sat3_nvenc.json ;;
dry)
  SECS=${2:-60}
  rm -f $STATE/dry.ts
  python3 tests/udp_rate_rx.py --port 12345 --seconds $SECS --skip 10 --out $STATE/dry.ts > $STATE/dry_rate.json &
  RX=$!
  start 127.0.0.1:12345 "${3:-examples/dvbs_uhd_nvenc.json}"
  wait $RX
  kill -TERM $(cat $STATE/pid); sleep 3; rm -f $STATE/pid
  python3 -c "
import json; r=json.load(open('$STATE/dry_rate.json'))
print('Probelauf: %.1f b/s empfangen ueber %.1f s (Soll 35 480 392,2), Abweichung %+.4f %%, %.1f TS-Pakete je Datagramm' % (r['rate_bps'], r['window_s'], (r['rate_bps']/35480392.157-1)*100, r['packets_per_datagram']))"
  tsp -I file $STATE/dry.ts -P continuity -P pcrverify -O drop 2>&1 | tail -1
  tsanalyze $STATE/dry.ts 2>/dev/null | grep -E "Service|HEVC|AVC|AAC" | head -8
  grep '"status"' $STATE/live.log | tail -1 | cut -c1-250
  ;;
stop)
  if [ -f $STATE/pid ]; then kill -TERM $(cat $STATE/pid) 2>/dev/null; sleep 4; rm -f $STATE/pid; fi
  echo "gestoppt; Windows-ffmpeg uebrig: $(/mnt/c/Windows/System32/tasklist.exe /FI 'IMAGENAME eq ffmpeg.exe' | grep -c ffmpeg.exe)"
  ;;
status)
  if [ -f $STATE/pid ] && kill -0 $(cat $STATE/pid) 2>/dev/null; then echo "laeuft (PID $(cat $STATE/pid))"; else echo "laeuft nicht"; fi
  grep '"status"' $STATE/live.log 2>/dev/null | tail -1 | cut -c1-300
  grep -E '"lvl": "(warn|error)"' $STATE/live.log 2>/dev/null | tail -3 | cut -c1-300
  ;;
ctl)
  SID=$(head -1 $STATE/live.log | python3 -c "import sys,json;print(json.load(sys.stdin)['session'])")
  python3 dablab_run.py ctl --session $SID "$2"
  ;;
*) sed -n 2,13p "$0" ;;
esac
