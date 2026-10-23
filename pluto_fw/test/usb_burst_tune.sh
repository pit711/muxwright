#!/bin/bash
# EXPERIMENT (noch nicht ausgeführt): iiod-USB-Durchsatz vs. ChipIdea-DMA-Burstgröße / ITC, zur Laufzeit per devmem.
# Git-Bash unter Windows; Pluto exklusiv, Nutzer anwesend (bei falschem Registerwert kann der USB-Controller hängen →
# Gerät neu einstecken; der Flash bleibt unberührt, nach Neustart gelten wieder die Treiberwerte).
#   bash usb_burst_tune.sh
# Register (Zynq TRM, USB0 @ 0xE0002000): USBCMD 0x140 (ITC Bits 23:16), SBUSCFG 0x090 (AHBBRST 2:0), BURSTSIZE 0x160
# (RXPBURST 7:0 = OUT vom PC, TXPBURST 15:8, Einheit 32-Bit-Worte). Bestes Ergebnis danach als DT-Eigenschaft in &usb0
# übernehmen (itc-setting, ahb-burst-config, rx-burst-size-dword, tx-burst-size-dword) – der Treiber setzt sie bei jedem Reset.
LIB=/g/DEEPSEEK/plutoTX/dablab/pc/libiio
SSH="wsl -d dablab -u root -- sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@192.168.2.1"
rd() { $SSH "devmem $1 32" 2>/dev/null | tr -d '\r'; }
wr() { $SSH "devmem $1 32 $2" 2>/dev/null; }
meas() {
  t0=$(date +%s.%N)
  head -c 200000000 /dev/zero | "$LIB/iio_writedev.exe" -u usb: -b 1048576 -T 0 cf-ad9361-dds-core-lpc voltage0 voltage1 > /dev/null 2>&1
  t1=$(date +%s.%N); awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f MB/s", 200/(b-a)}'
}
curl -s -X POST -d '{"rate_hz":30720000}' http://192.168.2.1:8080/api/rate > /dev/null
CMD0=$(rd 0xE0002140); SB0=$(rd 0xE0002090); BS0=$(rd 0xE0002160)
echo "Ausgang: USBCMD=$CMD0 SBUSCFG=$SB0 BURSTSIZE=$BS0  → $(meas)"
for bs in 0x00002020 0x00004040 0x00008080; do
  wr 0xE0002160 $bs; echo "BURSTSIZE=$bs → $(meas)"
done
wr 0xE0002160 $BS0
for itc in 0 2 8; do
  v=$(printf "0x%08X" $(( (CMD0 & ~0xFF0000) | (itc << 16) ))); wr 0xE0002140 $v; echo "ITC=$itc (USBCMD=$v) → $(meas)"
done
wr 0xE0002140 $CMD0; wr 0xE0002160 $BS0
echo "zurückgesetzt: USBCMD=$(rd 0xE0002140) BURSTSIZE=$(rd 0xE0002160)"
