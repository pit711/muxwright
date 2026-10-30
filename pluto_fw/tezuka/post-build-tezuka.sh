#!/bin/sh
# Muxwright auf Tezuka: läuft nach Tezukas und unserem post-build.sh.
# Versionszeile in /opt/VERSIONS (= iiod-Attribut fw_version, vom PC-Server und Agent gelesen) kennzeichnen.
set -e
V="$TARGET_DIR/opt/VERSIONS"
[ -f "$V" ] || { echo "post-build-tezuka: $V fehlt" >&2; exit 1; }
sed -i "1s/^device-fw \(.*\)$/device-fw \1+${MUXWRIGHT_VERSION:-muxwright}/" "$V"
head -1 "$V"
