#!/bin/bash
# Sat-TS mit 3 Diensten (sat_3dienste.ts: SID 1 HD H.264, SID 2 UHD HEVC, SID 3 HDR10 HEVC Main10; CBR 35480392 b/s = 22 MBd FEC 7/8) in Endlosschleife → UDP an den Pluto.
#   bash stream_hd.sh start [TS] [ZIEL] | stop | status
W=/opt/dablab/mod/stream; RATE=35480392
TS=${2:-$W/sat_3dienste.ts}; DST=${3:-192.168.2.1:1234}
case "$1" in
start)
  cd $W
  cat > nit_3.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="8" current="true" network_id="0xFF03" actual="true">
  <network_name_descriptor network_name="DABLAB Sat"/>
  <transport_stream transport_stream_id="0x0001" original_network_id="0xFF03">
   <satellite_delivery_system_descriptor frequency="11038000000" orbital_position="19.2" west_east_flag="east" polarization="vertical"
        roll_off="0.35" modulation_system="DVB-S" modulation_type="QPSK" symbol_rate="22000000" FEC_inner="7/8"/>
   <service_list_descriptor><service service_id="0x0001" service_type="0x19"/><service service_id="0x0002" service_type="0x1F"/><service service_id="0x0003" service_type="0x1F"/></service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
  NOW=$(date -u +"%Y-%m-%d %H:%M:%S")
  cat > eit_3.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <EIT type="pf" version="1" current="true" actual="true" service_id="0x0001" transport_stream_id="0x0001" original_network_id="0xFF03">
  <event event_id="1" start_time="$NOW" duration="12:00:00" running_status="running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Big Buck Bunny</event_name><text>Blender Foundation 2008, CC BY 3.0 – HD 1080p, H.264/AAC – DABLAB DVB-S-Testsender</text></short_event_descriptor>
  </event>
  <event event_id="2" start_time="$(date -u -d '+12 hours' +"%Y-%m-%d %H:%M:%S")" duration="12:00:00" running_status="not-running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Big Buck Bunny (Wiederholung)</event_name><text>Endlosschleife</text></short_event_descriptor>
  </event>
 </EIT>
 <EIT type="pf" version="1" current="true" actual="true" service_id="0x0002" transport_stream_id="0x0003" original_network_id="0xFF03">
  <event event_id="1" start_time="$NOW" duration="12:00:00" running_status="running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Big Buck Bunny UHD</event_name><text>Blender Foundation 2008, CC BY 3.0 – 4K 2160p25 HEVC/AAC – DABLAB DVB-S-Testsender</text></short_event_descriptor>
  </event>
 </EIT>
 <EIT type="pf" version="1" current="true" actual="true" service_id="0x0003" transport_stream_id="0x0003" original_network_id="0xFF03">
  <event event_id="1" start_time="$NOW" duration="12:00:00" running_status="running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Sol Levante (HDR10)</event_name><text>Netflix / Production I.G, CC BY 4.0 – 4K HDR10 HEVC Main10 – DABLAB DVB-S-Testsender</text></short_event_descriptor>
  </event>
 </EIT>
</tsduck>
EOF
  setsid nohup tsp -I file --infinite $TS -P continuity --fix -P pcradjust --bitrate $RATE \
       -P inject --pid 16 --bitrate 15000 --stuffing nit_3.xml -P inject --pid 18 --bitrate 15000 --stuffing eit_3.xml \
       -P regulate --bitrate $RATE -O ip --packet-burst 7 $DST > $W/stream_3.log 2>&1 &
  sleep 2; echo "gestartet: tsp $(pgrep -f 'tsp -I file --infinite') → udp://$DST"
  ;;
stop) pkill -f "tsp -I file --infinite"; echo gestoppt ;;
status) echo "tsp: $(pgrep -c -f 'tsp -I file --infinite')"; tail -3 $W/stream_3.log ;;
esac
