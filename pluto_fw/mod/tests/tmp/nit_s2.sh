#!/bin/bash
cd /tmp
for fec in undefined none auto; do for ro in 0.35; do
cat > n.xml <<X
<?xml version="1.0" encoding="UTF-8"?>
<tsduck>
 <NIT version="1" current="true" network_id="0x0001" actual="true">
  <transport_stream transport_stream_id="0x0001" original_network_id="0x0001">
   <satellite_delivery_system_descriptor frequency="11038000000" orbital_position="19.2" west_east_flag="east" polarization="vertical"
        roll_off="$ro" modulation_system="DVB-S2" modulation_type="8PSK" symbol_rate="22000000" FEC_inner="$fec"/>
  </transport_stream>
 </NIT>
</tsduck>
X
tstabcomp -o n.bin n.xml 2>&1 | tail -1
echo "$fec $ro → $(tstabcomp -d -o - n.bin 2>&1 | grep -o 'roll_off=[^/]*')"
done; done
