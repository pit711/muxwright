#!/bin/bash
# Live-TS (Video-Datei in Endlosschleife) → UDP an den Pluto. Läuft in WSL.
#   bash stream_video.sh start [VIDEO] [ZIEL]  |  stop  |  status
# MPEG-2 SD 720×576i 25 fps 16:9 (~10 Mbit/s), MP2 192k, CBR = exakte DVB-T-Nutzbitrate (8k/16-QAM/2/3/GI 1/4/8 MHz),
# Kennungen wie Testbild-TS: ONID/NID 0xFF01, TSID 1, SID 1, „DABLAB Test“; NIT 594 MHz/8 MHz; EIT p/f „Big Buck Bunny“.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"   # Repo-Wurzel (dablab/)
W=/opt/dablab/mod/stream; mkdir -p $W
RATE=13270588
VID=${2:-$ROOT/runtime/media/video/aqz-KE-bpKQ.mp4}
DST=${3:-192.168.2.1:1234}
case "$1" in
start)
  cd $W
  [ -f $W/src.mp4 ] || cp "$VID" $W/src.mp4
  cat > nit.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="5" current="true" network_id="0xFF01" actual="true">
  <network_name_descriptor network_name="DABLAB"/>
  <transport_stream transport_stream_id="0x0001" original_network_id="0xFF01">
   <terrestrial_delivery_system_descriptor centre_frequency="594000000" bandwidth="8MHz" priority="HP"
        no_time_slicing="true" no_MPE_FEC="true" constellation="16-QAM" hierarchy_information="0"
        code_rate_HP_stream="2/3" code_rate_LP_stream="1/2" guard_interval="1/4" transmission_mode="8k" other_frequency="false"/>
   <service_list_descriptor><service service_id="0x0001" service_type="0x01"/></service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
  NOW=$(date -u +"%Y-%m-%d %H:%M:%S")
  cat > eit.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <EIT type="pf" version="0" current="true" actual="true" service_id="0x0001" transport_stream_id="0x0001" original_network_id="0xFF01">
  <event event_id="1" start_time="$NOW" duration="12:00:00" running_status="running" CA_mode="false">
   <short_event_descriptor language_code="deu">
    <event_name>Big Buck Bunny</event_name>
    <text>Blender Foundation 2008, CC BY 3.0 – DABLAB DVB-T-Testsender</text>
   </short_event_descriptor>
  </event>
  <event event_id="2" start_time="$(date -u -d '+12 hours' +"%Y-%m-%d %H:%M:%S")" duration="12:00:00" running_status="not-running" CA_mode="false">
   <short_event_descriptor language_code="deu">
    <event_name>Big Buck Bunny (Wiederholung)</event_name>
    <text>Endlosschleife</text>
   </short_event_descriptor>
  </event>
 </EIT>
</tsduck>
EOF
  setsid nohup bash -c "nice -n 5 ffmpeg -loglevel warning -re -stream_loop -1 -i $W/src.mp4 \
     -vf 'scale=720:576,setsar=64/45,fps=25' -c:v mpeg2video -b:v 9500k -maxrate 9500k -minrate 9500k -bufsize 1835k -g 12 -bf 2 \
     -pix_fmt yuv420p -aspect 16:9 \
     -c:a mp2 -b:a 192k -ac 2 -ar 48000 \
     -mpegts_original_network_id 0xFF01 -mpegts_transport_stream_id 0x0001 -mpegts_service_id 1 \
     -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 -mpegts_service_type digital_tv \
     -metadata service_name='DABLAB Test' -metadata service_provider='DABLAB' \
     -muxrate $RATE -pcr_period 30 -f mpegts - \
   | tsp -I file - -P inject --pid 16 --bitrate 15000 --stuffing nit.xml \
         -P inject --pid 18 --bitrate 15000 --stuffing eit.xml \
         -P regulate --bitrate $RATE \
         -P analyze --interval 10 -o $W/analyze.txt \
         -O ip --packet-burst 7 $DST" > $W/stream.log 2>&1 &
  sleep 3
  pgrep -f "tsp -I file" > $W/tsp.pid; pgrep -f "ffmpeg -loglevel warning -re -stream_loop" > $W/ffmpeg.pid
  echo "gestartet: ffmpeg $(cat $W/ffmpeg.pid) tsp $(cat $W/tsp.pid) → udp://$DST, $RATE b/s"
  ;;
stop)
  pkill -f "ffmpeg -loglevel warning -re -stream_loop"; pkill -f "tsp -I file -"; echo gestoppt ;;
status)
  echo "ffmpeg: $(pgrep -c -f 'ffmpeg -loglevel warning -re -stream_loop') tsp: $(pgrep -c -f 'tsp -I file -')"
  tail -3 $W/stream.log
  grep -E "Average bitrate|Transport stream bitrate|Discontinuities|Invalid sync|Transport errors" $W/analyze.txt 2>/dev/null | head -5
  ;;
esac
