#!/bin/bash
# Baut die ODR-mmbTools + welle-cli in der WSL1-Distribution „dablab“ nach /opt/dablab/bin.
# Aufruf (als root): wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/wsl/build_tools.sh
set -u
export DEBIAN_FRONTEND=noninteractive
printf "nameserver 1.1.1.1
nameserver 8.8.8.8
" > /etc/resolv.conf
echo "Acquire::ForceIPv4 \"true\";" > /etc/apt/apt.conf.d/99ipv4
echo "=== apt update"; apt-get update -qq || echo "=== apt update FEHLER"
P=/opt/dablab; S=/opt/dablab/src; mkdir -p $P/bin $S
LOG="$(cd "$(dirname "$0")" && pwd)/build.log"
exec > >(tee -a $LOG) 2>&1
echo "=== $(date) Pakete"
apt-get install -y -qq git build-essential automake autoconf libtool pkg-config cmake \
  libzmq3-dev libcurl4-openssl-dev libfftw3-dev libboost-dev libmagickwand-dev imagemagick \
  libfaad-dev libmpg123-dev libmp3lame-dev libusb-1.0-0-dev librtlsdr-dev \
  ffmpeg python3 python3-numpy python3-scipy netcat-openbsd >/dev/null || echo "=== apt FEHLER"
cd $S
for r in ODR-AudioEnc ODR-PadEnc ODR-DabMux ODR-DabMod; do
  [ -d $r ] || git clone -q --depth 1 https://github.com/Opendigitalradio/$r.git
done
[ -d welle.io ] || git clone -q --depth 1 https://github.com/AlbrechtL/welle.io.git
b() { echo "=== $1"; cd $S/$1 && { [ -x configure ] || ./bootstrap* >/dev/null 2>&1 || autoreconf -fi >/dev/null 2>&1; } \
      && ./configure --prefix=$P $2 >/dev/null && make -j"$(nproc)" 2>&1 | grep -E "error:" | head -15; make install >/dev/null 2>&1; echo "=== $1 rc=$?"; }
b ODR-AudioEnc "--disable-alsa --disable-jack --disable-gst --disable-vlc"
b ODR-PadEnc ""
b ODR-DabMux ""
b ODR-DabMod "--disable-output-uhd --disable-native --disable-limesdr --disable-bladerf --disable-soapysdr"
echo "=== welle"
cd $S/welle.io && mkdir -p build && cd build && cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=OFF -DSOAPYSDR=OFF \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null && make -j"$(nproc)" welle-cli 2>&1 | grep -E "error:" | head; cp welle-cli $P/bin/; echo "=== welle rc=$?"
ls -la $P/bin
echo "=== $(date) fertig"
