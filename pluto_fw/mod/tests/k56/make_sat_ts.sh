#!/bin/bash
# DVB-S-Test-TS „DABLAB Sat“: ONID 0xFF03, TSID 3, SID 1, NIT satellite_delivery (Frequenz, 19,2° O, Polarisation, SR, FEC)
set -e
W=/opt/dablab/mod/sat; mkdir -p $W; cd $W
SR=${SR:-22000000}; FECN=${FECN:-1}; FECD=${FECD:-2}; FREQ=${FREQ:-11038000000}; POL=${POL:-vertical}; SECS=${SECS:-20}
RATE=$(python3 -c "print(round($SR*2*$FECN/$FECD*188/204))")
echo "Nutzbitrate $RATE b/s"
cat > nit.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="1" current="true" network_id="0xFF03" actual="true">
  <network_name_descriptor network_name="DABLAB Sat"/>
  <transport_stream transport_stream_id="0x0003" original_network_id="0xFF03">
   <satellite_delivery_system_descriptor frequency="$FREQ" orbital_position="19.2" west_east_flag="east" polarization="$POL"
        roll_off="0.35" modulation_system="DVB-S" modulation_type="QPSK" symbol_rate="$SR" FEC_inner="$FECN/$FECD"/>
   <service_list_descriptor><service service_id="0x0001" service_type="0x01"/></service_list_descriptor>
  </transport_stream>
 </NIT>
</tsduck>
EOF
nice -n 10 ffmpeg -loglevel error -y -f lavfi -i "pal75bars=size=720x576:rate=25" -f lavfi -i "sine=frequency=1000:sample_rate=48000" -t $SECS \
  -vf "drawtext=text='DABLAB Sat DVB-S $((FREQ/1000000)) MHz ${POL:0:1} $((SR/1000)) kS/s FEC $FECN/$FECD %{pts\:hms}':fontcolor=white:fontsize=24:x=30:y=520:box=1:boxcolor=black@0.6" \
  -c:v mpeg2video -b:v 8M -maxrate 8M -minrate 8M -bufsize 1835k -g 12 -bf 2 -pix_fmt yuv420p -aspect 16:9 \
  -c:a mp2 -b:a 192k -ac 2 \
  -mpegts_original_network_id 0xFF03 -mpegts_transport_stream_id 0x0003 -mpegts_service_id 1 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 \
  -mpegts_service_type digital_tv -metadata service_name="DABLAB Sat" -metadata service_provider="DABLAB" \
  -muxrate $RATE -f mpegts raw.ts
tsp -I file raw.ts -P inject --pid 16 --bitrate 15000 --stuffing nit.xml -P continuity -O file sat.ts 2>&1 | tail -2
ls -la sat.ts
tsanalyze sat.ts | grep -E "Service name" | head -2
tstables --pid 16 --max-tables 1 sat.ts 2>/dev/null | grep -E "Orbital|Frequency|Polar|Symbol|FEC|Modulation" | head -6
