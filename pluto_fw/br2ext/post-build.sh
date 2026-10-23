#!/bin/sh
# DAB-Lab Post-Build (läuft NACH board/pluto/post-build.sh von ADI):
#  - S23udc: iiod per taskset auf wählbaren Kern (U-Boot-Variable iiod_cpu, Maske, Standard 2 = Kern 1),
#            Netzfunktion rndis|ncm wählbar (U-Boot-Variable usb_net, Standard rndis)
#  - Init-Skripte aus dem Overlay ausführbar machen
set -e
T="${TARGET_DIR}"
UDC="$T/etc/init.d/S23udc"

if [ -f "$UDC" ] && ! grep -q DABLAB "$UDC"; then
	# Variablen nach UDC_HANDLE_SUSPEND einfügen
	sed -i '/^UDC_HANDLE_SUSPEND=/a\
# DABLAB: iiod-CPU-Maske (1=Kern0, 2=Kern1, 3=beide) und USB-Netzfunktion (rndis|ncm)\
IIOD_CPU=`fw_printenv -n iiod_cpu 2> /dev/null || echo 2`\
USB_NET=`fw_printenv -n usb_net 2> /dev/null || echo rndis`\
case "$USB_NET" in ncm|ecm|rndis) ;; *) USB_NET=rndis ;; esac\
NETFN=$USB_NET.0' "$UDC"
	sed -i 's#functions/rndis\.0#functions/$NETFN#g; s#configs/c\.1/rndis\.0#configs/c.1/$NETFN#g' "$UDC"
	# iiod wie im Original starten (USB-Gadget hängt davon ab!), Affinität danach setzen
	sed -i '/start-stop-daemon -S -b -q -m -p \/var\/run\/iiod.pid -x  \/usr\/sbin\/iiod  -- $IIOD_OPTS/a\
	sleep 0.2; [ -x /usr/bin/taskset ] \&\& /usr/bin/taskset -p $IIOD_CPU `cat /var/run/iiod.pid` > /dev/null 2>\&1 # DABLAB' "$UDC"
	grep -q 'taskset -p $IIOD_CPU' "$UDC" || { echo "S23udc: iiod-Zeile nicht gefunden" >&2; exit 1; }
fi
# S45msd liest rndis.0/host_addr für die Infoseite – bei ncm tolerant machen
sed -i 's#functions/rndis.0/host_addr#functions/*.0/host_addr#; s#functions/rndis.0/dev_addr#functions/*.0/dev_addr#' "$T/etc/init.d/S45msd" 2>/dev/null || true

chmod 0755 "$T"/etc/init.d/S22dablab "$T"/etc/init.d/S95dablab-agent
# Platz für Modulator-Dateien
mkdir -p "$T/opt/dablab"
echo "dablab post-build OK"
