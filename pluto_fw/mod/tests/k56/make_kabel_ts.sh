#!/bin/bash
# DVB-C-Test-TS: Testbild SD MPEG-2 + MP2, „DABLAB Kabel“, ONID 0xFF02, TSID 2, SID 1, NIT cable_delivery 546 MHz/64-QAM/6900 kBd
set -e
W=/opt/dablab/mod/kabel; mkdir -p $W; cd $W
QAM=${QAM:-64}; SR=${SR:-6900000}; FREQ=${FREQ:-546000000}; SECS=${SECS:-20}
M=$(python3 -c "import math;print(int(math.log2($QAM)))")
RATE=$(python3 -c "print(round($SR*$M*188/204))")
echo "Nutzbitrate $RATE b/s"
cat > nit.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="1" current="true" network_id="0xFF02" actual="true">
  <network_name_descriptor network_name="DABLAB Kabel"/>
  <transport_stream transport_stream_id="0x0002" original_network_id="0xFF02">
   <cable_delivery_system_descriptor frequency="$FREQ" FEC_outer="RS" modulation="$QAM-QAM" symbol_rate="$SR" FEC_inner="none"/>
   <service_list_descriptor><service service_id="0x0001" service_type="0x01"/></service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
nice -n 10 ffmpeg -loglevel error -y -f lavfi -i "pal75bars=size=720x576:rate=25" -f lavfi -i "sine=frequency=1000:sample_rate=48000" -t $SECS \
  -vf "drawtext=text='DABLAB Kabel DVB-C $((FREQ/1000000)) MHz ${QAM}-QAM $((SR/1000)) kBd %{pts\:hms}':fontcolor=white:fontsize=24:x=30:y=520:box=1:boxcolor=black@0.6" \
  -c:v mpeg2video -b:v 8M -maxrate 8M -minrate 8M -bufsize 1835k -g 12 -bf 2 -pix_fmt yuv420p -aspect 16:9 \
  -c:a mp2 -b:a 192k -ac 2 \
  -mpegts_original_network_id 0xFF02 -mpegts_transport_stream_id 0x0002 -mpegts_service_id 1 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 \
  -mpegts_service_type digital_tv -metadata service_name="DABLAB Kabel" -metadata service_provider="DABLAB" \
  -muxrate $RATE -f mpegts raw.ts
tsp -I file raw.ts -P inject --pid 16 --bitrate 15000 --stuffing nit.xml -P continuity -O file kabel.ts 2>&1 | tail -2
ls -la kabel.ts
tsanalyze kabel.ts | grep -E "Service name|Bitrate: \." | head -3
tstables --pid 16 --max-tables 1 kabel.ts 2>/dev/null | grep -E "Frequency|Modulation|Symbol" | head -3
