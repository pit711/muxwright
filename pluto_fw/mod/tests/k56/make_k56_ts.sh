#!/bin/bash
# TS für DVB-T K56 (754 MHz), 8k 16-QAM 2/3 GI 1/4: Testbild + 1 kHz, NIT mit terrestrial_delivery_system_descriptor
set -e
W=/opt/dablab/mod/k56; mkdir -p $W; cd $W
M=/opt/dablab/mod/src/dvbt_mod
RATE=$($M --mode 8k --const 16qam --rate 2/3 --guard 1/4 --bench 1 2>&1 | head -1 | python3 -c "import json,sys;print(round(json.loads(sys.stdin.readline())['bitrate_bps']))")
echo "Nutzbitrate: $RATE"
cat > nit.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="1" current="true" network_id="0xFF01" actual="true">
  <network_name_descriptor network_name="DABLAB"/>
  <transport_stream transport_stream_id="0x0001" original_network_id="0xFF01">
   <terrestrial_delivery_system_descriptor centre_frequency="754000000" bandwidth="8MHz" priority="HP"
        no_time_slicing="true" no_MPE_FEC="true" constellation="16-QAM" hierarchy_information="0"
        code_rate_HP_stream="2/3" code_rate_LP_stream="1/2" guard_interval="1/4" transmission_mode="8k" other_frequency="false"/>
   <service_list_descriptor>
    <service service_id="0x0001" service_type="0x01"/>
   </service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
nice -n 10 ffmpeg -loglevel error -y -f lavfi -i "smptehdbars=size=720x576:rate=25" -f lavfi -i "sine=frequency=1000:sample_rate=48000" -t 40 \
  -vf "drawtext=text='DABLAB K56 DVB-T 8k 16QAM 2/3 GI1/4 %{pts\:hms}':fontcolor=white:fontsize=26:x=40:y=520:box=1:boxcolor=black@0.6" \
  -c:v mpeg2video -b:v 7M -maxrate 7M -minrate 7M -bufsize 1835k -g 12 -bf 2 -pix_fmt yuv420p -aspect 16:9 \
  -c:a mp2 -b:a 192k -ac 2 \
  -mpegts_original_network_id 0xFF01 -mpegts_transport_stream_id 0x0001 -mpegts_service_id 1 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 \
  -mpegts_service_type digital_tv -metadata service_name="DABLAB Test" -metadata service_provider="DABLAB" \
  -muxrate $RATE -f mpegts raw.ts 2>&1 | tail -3 || \
nice -n 10 ffmpeg -loglevel error -y -f lavfi -i "smptehdbars=size=720x576:rate=25" -f lavfi -i "sine=frequency=1000:sample_rate=48000" -t 40 \
  -c:v mpeg2video -b:v 7M -maxrate 7M -minrate 7M -bufsize 1835k -g 12 -bf 2 -pix_fmt yuv420p -aspect 16:9 \
  -c:a mp2 -b:a 192k -ac 2 \
  -mpegts_original_network_id 0xFF01 -mpegts_transport_stream_id 0x0001 -mpegts_service_id 1 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 \
  -mpegts_service_type digital_tv -metadata service_name="DABLAB Test" -metadata service_provider="DABLAB" \
  -muxrate $RATE -f mpegts raw.ts
ls -la raw.ts
# NIT auf PID 16 aus Stopfpaketen einfügen (Bitrate bleibt), CC prüfen
tsp -I file raw.ts -P inject --pid 16 --bitrate 15000 --stuffing nit.xml -P continuity -O file k56.ts 2>&1 | tail -3
ls -la k56.ts
tsanalyze k56.ts | grep -E "Transport Stream Id|Bitrate|Services|Service Name|Provider|PIDs|NIT|Null|DABLAB" | head -20
tstables --pid 16 --max-tables 1 k56.ts 2>/dev/null | head -20
# Prüfung: Modulation (Fenster 32) + gr-dtv-Decodierung der ersten 10 s
head -c $(( (RATE/8/188)*188*10 )) k56.ts > k56_10s.ts
$M -i k56_10s.ts --mode 8k --const 16qam --rate 2/3 --guard 1/4 --window 32 --threads 1 -o k56.iq --quiet
timeout 900 python3 /opt/dablab/mod/src/tests/dvbt_rx_grdtv.py --iq k56.iq --mode 8k --const 16qam --rate 2/3 --guard 1/4 -o k56_rx.ts > /dev/null 2>&1
python3 /opt/dablab/mod/tscmp.py k56_rx.ts k56_10s.ts | tail -1
python3 /opt/dablab/mod/src/tests/iq_analyze.py --iq k56.iq --rate 9142857.142857 --bw 8 --skip 20000 | grep -E "shoulder|rms|papr\""
rm -f k56.iq
cp k56.ts ${OUT:-/tmp/dablab}/k56.ts
