#!/bin/bash
# Installiert/baut eine Komponente in der WSL-Umgebung „dablab“ – ausgelöst vom Nutzer in der Einrichtung (pc/setup.py).
# Alles kommt aus den offiziellen Quellen (Ubuntu-Paketquellen, GitHub der Projekte); Muxwright liefert keine Codecs mit.
#   bash setup_components.sh <Muxwright-Ordner als WSL-Pfad> <komponente|check>
# Komponenten: base ffmpeg tsduck odr welle ytdlp modulators   ·   check: Status aller Komponenten als JSON
# Ausgabe: Zeilen „@@STEP <text>“, „@@OK <komponente>“, „@@FAIL <komponente> <grund>“
set -u -o pipefail
ROOT=${1:?Muxwright-Ordner fehlt}; WHAT=${2:?Komponente fehlt}
export DEBIAN_FRONTEND=noninteractive PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
P=/opt/dablab; S=$P/src; mkdir -p $P/bin $S $P/mod
fail() { echo "@@FAIL $WHAT $*"; exit 1; }
step() { echo "@@STEP $*"; }
net() {   # DNS-Fallback nur, wenn die Namensauflösung nicht geht (WSL mit VPN o. ä.)
  getent hosts archive.ubuntu.com >/dev/null 2>&1 || printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
}
apt_in() { step "apt install $*"; apt-get install -y -q "$@" 2>&1 | grep -E "^(E:|Err)" ; [ "${PIPESTATUS[0]}" = 0 ] || fail "apt-get install $*"; }
apt_upd() { [ -n "$(find /var/lib/apt/lists -maxdepth 1 -name '*Packages*' -mmin -600 2>/dev/null | head -1)" ] || { step "apt update"; apt-get update -q >/dev/null || fail "apt-get update"; }; }

# WSL legt für Linux-Programme mit Oberfläche (z. B. ImageMagick „display“) Startmenü-Einträge im Ordner der Distro an –
# die Distro heißt wie das Muxwright-Startmenü. Solche Einträge ausblenden (NoDisplay); Terminal-Programme bleiben unberührt.
hide_gui_entries() {
  for f in /usr/share/applications/*.desktop; do
    [ -f "$f" ] || continue
    grep -qi '^NoDisplay=true' "$f" && continue
    grep -qi '^Terminal=true' "$f" && continue
    sed -i '0,/^\[Desktop Entry\]/s//[Desktop Entry]\nNoDisplay=true/' "$f"
  done
}
[ "$WHAT" = check ] || trap hide_gui_entries EXIT

have_base()   { command -v python3 >/dev/null && python3 -c "import numpy,scipy" 2>/dev/null && command -v git >/dev/null && command -v make >/dev/null; }
have_ffmpeg() { command -v ffmpeg >/dev/null && [[ "$(ffmpeg -hide_banner -encoders 2>/dev/null)" == *libx264* ]]; }   # ohne Pipe (pipefail/SIGPIPE)
have_tsduck() { command -v tsp >/dev/null; }
# ODR mit Muxwright-Patches (runner/patches: FIFO-Lesen ohne „rewind“, Encoder-Warteschleife, negatives LTO)
have_odr()    { for b in odr-audioenc odr-padenc odr-dabmux odr-dabmod; do [ -x $P/bin/$b ] || return 1; done
                grep -q DABLAB_FIFO $S/ODR-DabMux/src/input/File.cpp 2>/dev/null && grep -q DABLAB_QUEUE $S/ODR-AudioEnc/src/SampleQueue.h 2>/dev/null; }
have_welle()  { [ -x $P/bin/welle-cli ] && [ -x $P/bin/redsea ] && grep -rqs DABLAB_DLPLUS $S/welle.io/src/welle-cli; }
have_ytdlp()  { command -v yt-dlp >/dev/null; }
have_modulators() { for b in dvbt_mod dvbc_mod dvbs_mod dvbs2_mod rrc_fir_gen; do [ -x $P/mod/src/$b ] || return 1; done; }

case "$WHAT" in
check)
  printf '{'
  sep=""
  for c in base ffmpeg tsduck odr welle ytdlp modulators; do
    if have_$c; then v=true; else v=false; fi
    printf '%s"%s":%s' "$sep" "$c" "$v"; sep=","
  done
  printf ',"ubuntu":"%s"}\n' "$(. /etc/os-release 2>/dev/null; echo "${VERSION_ID:-?}")"
  exit 0 ;;
base)
  have_base && { echo "@@OK base (vorhanden)"; exit 0; }
  net; apt_upd
  apt_in git build-essential automake autoconf libtool pkg-config cmake wget curl ca-certificates xz-utils \
    python3 python3-numpy python3-scipy python3-pip netcat-openbsd sshpass libiio-utils imagemagick \
    libzmq3-dev libcurl4-openssl-dev libfftw3-dev libboost-dev libmagickwand-dev libfaad-dev libmpg123-dev \
    libmp3lame-dev libusb-1.0-0-dev
  have_base || fail "Python/numpy/scipy oder Build-Werkzeuge fehlen danach"
  echo "@@OK base" ;;
ffmpeg)
  have_ffmpeg && { echo "@@OK ffmpeg (vorhanden)"; exit 0; }
  net; apt_upd; apt_in ffmpeg
  have_ffmpeg || fail "ffmpeg ohne libx264"
  echo "@@OK ffmpeg" ;;
tsduck)
  have_tsduck && { echo "@@OK tsduck (vorhanden)"; exit 0; }
  net
  VER=$(. /etc/os-release; echo "${VERSION_ID%%.*}")
  step "TSDuck: neueste Version von github.com/tsduck/tsduck suchen"
  URL=$(curl -fsSL https://api.github.com/repos/tsduck/tsduck/releases/latest | grep -o "https://[^\"]*ubuntu${VER}_amd64\.deb" | grep -v -- "-dev" | head -1)
  [ -n "$URL" ] || fail "kein TSDuck-Paket für Ubuntu $VER gefunden"
  step "TSDuck laden: $URL"
  curl -fsSL -o /tmp/tsduck.deb "$URL" || fail "Download $URL"
  apt_upd; apt_in /tmp/tsduck.deb; rm -f /tmp/tsduck.deb
  have_tsduck || fail "tsp fehlt nach der Installation"
  echo "@@OK tsduck" ;;
odr)
  have_odr && { echo "@@OK odr (vorhanden)"; exit 0; }
  have_base || fail "zuerst „Grundpakete“ installieren"
  net; cd $S
  for r in ODR-AudioEnc ODR-PadEnc ODR-DabMux ODR-DabMod; do
    [ -d $r ] || { step "git clone github.com/Opendigitalradio/$r"; git clone -q --depth 1 https://github.com/Opendigitalradio/$r.git || fail "git clone $r"; }
  done
  b() { step "baue $1 (dauert einige Minuten)"; cd $S/$1 && { [ -x configure ] || ./bootstrap* >/dev/null 2>&1 || autoreconf -fi >/dev/null 2>&1; } \
        && ./configure --prefix=$P $2 >/dev/null && make -j"$(nproc)" >/tmp/odr_build.log 2>&1 && make install >/dev/null 2>&1 \
        || { tail -5 /tmp/odr_build.log; fail "Bauen von $1"; }; }
  step "Muxwright-Patches anwenden (runner/patches)"
  python3 "$ROOT/runner/patches/patch_audioenc.py" $S/ODR-AudioEnc >/dev/null || fail "Patch ODR-AudioEnc"
  python3 "$ROOT/runner/patches/patch_dabmux.py" $S/ODR-DabMux >/dev/null || fail "Patch ODR-DabMux"
  b ODR-AudioEnc "--disable-alsa --disable-jack --disable-gst --disable-vlc"
  b ODR-PadEnc ""
  b ODR-DabMux ""
  b ODR-DabMod "--disable-output-uhd --disable-native --disable-limesdr --disable-bladerf --disable-soapysdr"
  have_odr || fail "ODR-Programme fehlen nach dem Bauen"
  echo "@@OK odr" ;;
welle)
  have_welle && { echo "@@OK welle (vorhanden)"; exit 0; }
  have_base || fail "zuerst „Grundpakete“ installieren"
  net; cd $S
  [ -d welle.io ] || { step "git clone github.com/AlbrechtL/welle.io"; git clone -q --depth 1 https://github.com/AlbrechtL/welle.io.git || fail "git clone welle.io"; }
  python3 "$ROOT/runner/patches/patch_welle.py" $S/welle.io >/dev/null || fail "Patch welle.io"
  step "baue welle-cli"
  cd $S/welle.io && mkdir -p build && cd build && cmake .. -DBUILD_WELLE_IO=OFF -DBUILD_WELLE_CLI=ON -DRTLSDR=OFF -DSOAPYSDR=OFF \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null && make -j"$(nproc)" welle-cli >/tmp/welle_build.log 2>&1 && cp welle-cli $P/bin/ \
    || { tail -5 /tmp/welle_build.log; fail "Bauen von welle-cli"; }
  # redsea (MIT): RDS-Decoder für den UKW-Kontrollempfang
  apt_upd; apt_in meson ninja-build libliquid-dev libsndfile1-dev nlohmann-json3-dev
  cd $S; [ -d redsea ] || { step "git clone github.com/windytan/redsea"; git clone -q --depth 1 https://github.com/windytan/redsea.git || fail "git clone redsea"; }
  step "baue redsea"
  cd $S/redsea && { [ -d build ] || meson setup build >/tmp/redsea_build.log 2>&1; } && ninja -C build >>/tmp/redsea_build.log 2>&1 \
    && cp build/redsea $P/bin/ || { tail -5 /tmp/redsea_build.log; fail "Bauen von redsea"; }
  have_welle || fail "welle-cli/redsea fehlen nach dem Bauen"
  echo "@@OK welle" ;;
ytdlp)
  have_ytdlp && { echo "@@OK ytdlp (vorhanden)"; exit 0; }
  net; step "pip install yt-dlp"
  python3 -m pip install -q --break-system-packages -U yt-dlp >/dev/null 2>&1 || fail "pip install yt-dlp"
  echo "@@OK ytdlp" ;;
modulators)
  have_base || fail "zuerst „Grundpakete“ installieren"
  step "baue Muxwright-Modulatoren (eigener Code, MIT)"
  rm -rf $P/mod/src && cp -r "$ROOT/pluto_fw/mod" $P/mod/src && cd $P/mod/src && make clean >/dev/null 2>&1
  make -j"$(nproc)" >/tmp/mod_build.log 2>&1 || { tail -5 /tmp/mod_build.log; fail "make"; }
  [[ "$(./dvbmod_selftest 2>&1)" == *"SELFTEST OK"* ]] || fail "Selbsttest der Modulatoren"
  have_modulators || fail "Modulatoren fehlen"
  echo "@@OK modulators" ;;
*) fail "unbekannte Komponente" ;;
esac
