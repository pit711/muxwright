#!/bin/bash
# HD-TS (vorab transkodiert: H.264 1080p25 + AAC-LC, CBR 13 270 588 b/s) in Endlosschleife → UDP an den Pluto.
#   bash stream_hd.sh start [TS] [ZIEL] | stop | status
W=/opt/dablab/mod/stream; RATE=13270588
TS=${2:-$W/bbb_hd.ts}; DST=${3:-192.168.2.1:1234}
case "$1" in
start)
  cd $W
  cat > nit_hd.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="6" current="true" network_id="0xFF01" actual="true">
  <network_name_descriptor network_name="DABLAB"/>
  <transport_stream transport_stream_id="0x0001" original_network_id="0xFF01">
   <terrestrial_delivery_system_descriptor centre_frequency="594000000" bandwidth="8MHz" priority="HP"
        no_time_slicing="true" no_MPE_FEC="true" constellation="16-QAM" hierarchy_information="0"
        code_rate_HP_stream="2/3" code_rate_LP_stream="1/2" guard_interval="1/4" transmission_mode="8k" other_frequency="false"/>
   <service_list_descriptor><service service_id="0x0001" service_type="0x19"/></service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
  NOW=$(date -u +"%Y-%m-%d %H:%M:%S")
  cat > eit_hd.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <EIT type="pf" version="1" current="true" actual="true" service_id="0x0001" transport_stream_id="0x0001" original_network_id="0xFF01">
  <event event_id="1" start_time="$NOW" duration="12:00:00" running_status="running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Big Buck Bunny</event_name><text>Blender Foundation 2008, CC BY 3.0 – HD 1080p, H.264/AAC – DABLAB DVB-T-Testsender</text></short_event_descriptor>
  </event>
  <event event_id="2" start_time="$(date -u -d '+12 hours' +"%Y-%m-%d %H:%M:%S")" duration="12:00:00" running_status="not-running" CA_mode="false">
   <short_event_descriptor language_code="deu"><event_name>Big Buck Bunny (Wiederholung)</event_name><text>Endlosschleife</text></short_event_descriptor>
  </event>
 </EIT>
</tsduck>
EOF
  setsid nohup tsp -I file --infinite $TS -P continuity --fix -P pcradjust --bitrate $RATE \
       -P inject --pid 16 --bitrate 15000 --stuffing nit_hd.xml -P inject --pid 18 --bitrate 15000 --stuffing eit_hd.xml \
       -P regulate --bitrate $RATE -O ip --packet-burst 7 $DST > $W/stream_hd.log 2>&1 &
  sleep 2; echo "gestartet: tsp $(pgrep -f 'tsp -I file --infinite') → udp://$DST"
  ;;
stop) pkill -f "tsp -I file --infinite"; echo gestoppt ;;
status) echo "tsp: $(pgrep -c -f 'tsp -I file --infinite')"; tail -3 $W/stream_hd.log ;;
esac
