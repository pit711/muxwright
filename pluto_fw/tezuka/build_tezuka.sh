#!/usr/bin/env bash
# Muxwright-Firmware für Pluto-Klone auf Basis von Tezuka (F5OEO/tezuka_fw):
# Tezuka-Board (Kernel, Devicetree, FPGA-Bitstream, SD-Boot) + Muxwright-Pakete aus pluto_fw/br2ext
# (dablab-agent, iqplay, dablab-mod, Init-Skripte). Zwei BR2_EXTERNAL-Bäume, Tezuka bleibt unverändert.
#
#   wsl -d Ubuntu-24.04 -u root -- bash /mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/tezuka/build_tezuka.sh [board]
#   board = Tezuka-Name aus boards.json (Standard: fishball = Fishball/PlutoSky Zynq 7010)
# Ergebnis: pluto_fw/out/tezuka/<board>/ (sdimg/, pluto.frm, pluto.dfu, SHA256SUMS)
set -euo pipefail

BOARD=${1:-fishball}
TEZUKA_REV=${TEZUKA_REV:-a304a1f}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW="$(dirname "$HERE")"                       # pluto_fw
W=/root/mx                                    # Build auf ext4 (WSL2), nicht auf /mnt
T=$W/tezuka_fw
D=$W/dablab_fw
VERSION=${VERSION:-muxwright.$(date +%Y%m%d)}

mkdir -p $W
if ! dpkg -s jq libncurses-dev u-boot-tools clang libclang-dev nodejs npm >/dev/null 2>&1; then
	apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential jq libncurses-dev unzip bc cpio rsync file \
		wget git python3 python3-setuptools libssl-dev flex bison gawk texinfo u-boot-tools device-tree-compiler zip \
		cmake pkg-config libgnutls28-dev uuid-dev clang libclang-dev nodejs npm >/dev/null
fi
# ARM-Rechner (z. B. Lima-VM auf Apple Silicon mit Rosetta): Tezukas Bootlin-Toolchain gibt es nur für x86-64,
# sie läuft über Rosetta/binfmt und braucht die amd64-Laufzeitbibliotheken.
if [ "$(uname -m)" = aarch64 ] && ! dpkg -s libc6:amd64 >/dev/null 2>&1; then
	dpkg --add-architecture amd64
	sed -i 's/^Types: deb$/Types: deb\nArchitectures: arm64/' /etc/apt/sources.list.d/ubuntu.sources
	cat > /etc/apt/sources.list.d/amd64.sources <<EOF
Types: deb
URIs: http://archive.ubuntu.com/ubuntu
Suites: noble noble-updates noble-security
Components: main universe
Architectures: amd64
EOF
	apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libc6:amd64 libstdc++6:amd64 zlib1g:amd64 libzstd1:amd64 >/dev/null
fi

if [ ! -d $T/.git ]; then
	git clone https://github.com/F5OEO/tezuka_fw.git $T
fi
( cd $T && git fetch -q --tags origin && git checkout -q $TEZUKA_REV && ./getbuildroot.sh )

# Muxwright-Quellen spiegeln (br2ext erwartet agent/iqplay/mod als Geschwister)
mkdir -p $D
rsync -a --delete --exclude '*.orig' $FW/br2ext/ $D/br2ext/
for s in agent iqplay mod; do rsync -a --delete --exclude '*.o' $FW/$s/ $D/$s/; done
# Die Host-Fixes in br2ext/patches gelten für Buildroot 2019 – hier nicht verwenden
rm -rf $D/br2ext/patches
install -m 0755 $HERE/post-build-tezuka.sh $D/post-build-tezuka.sh
chmod 0755 $D/br2ext/post-build.sh

DEFC=$(jq -r --arg b "$BOARD" '.[] | select(.board==$b) | .defconfig' $T/boards.json)
[ -n "$DEFC" ] || { echo "Board $BOARD nicht in boards.json" >&2; exit 1; }
MXDEFC=${DEFC%_defconfig}_muxwright_defconfig
# Tezuka-Defconfig übernehmen, Overlay und Post-Build um Muxwright erweitern, Pakete anhängen
sed -e "s#^\(BR2_ROOTFS_OVERLAY=\".*\)\"#\1 $D/br2ext/overlay\"#" \
    -e "s#^\(BR2_ROOTFS_POST_BUILD_SCRIPT=\".*\)\"#\1 $D/br2ext/post-build.sh $D/post-build-tezuka.sh\"#" \
    $T/configs/$DEFC > $T/configs/$MXDEFC
cat >> $T/configs/$MXDEFC <<EOF

##### Muxwright #####
BR2_PACKAGE_DABLAB_AGENT=y
BR2_PACKAGE_IQPLAY=y
BR2_PACKAGE_DABLAB_MOD=y
BR2_PACKAGE_IPERF3=y
EOF
grep -q "$D/br2ext/overlay" $T/configs/$MXDEFC || { echo "Overlay-Zeile nicht erweitert" >&2; exit 1; }
grep -q "post-build-tezuka.sh" $T/configs/$MXDEFC || { echo "Post-Build-Zeile nicht erweitert" >&2; exit 1; }

export CMAKE_POLICY_VERSION_MINIMUM=3.5 MUXWRIGHT_VERSION=$VERSION
unset LD_LIBRARY_PATH
O=$T/output/${BOARD}_mx
make -C $T/buildroot O=$O BR2_EXTERNAL=$T:$D/br2ext $MXDEFC
nice -n 10 make -C $T/buildroot O=$O -j$(nproc)

OUT=$FW/out/tezuka/$BOARD
rm -rf $OUT; mkdir -p $OUT
I=$O/images
[ -d $I/sdimg ] && cp -r $I/sdimg $OUT/
for f in pluto.frm pluto.dfu boot.frm boot.dfu; do [ -f $I/$f ] && cp $I/$f $OUT/; done
[ -d $I/flash ] && cp -r $I/flash $OUT/
cp $O/target/opt/VERSIONS $OUT/VERSIONS
sed -n "s/^device-fw //p" $OUT/VERSIONS > $OUT/VERSION
( cd $OUT && find . -type f ! -name SHA256SUMS -exec sha256sum {} + | sort -k2 > SHA256SUMS )
echo "fertig: $OUT"; cat $OUT/VERSIONS
