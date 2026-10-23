#!/bin/bash
# Zwei kurze getaggte Testtitel (MP3 + OGG) und die Playlist "testliste" (per M3U-Import) anlegen
cd "$(dirname "$0")/.."
M=$(python3 -c "import sys; sys.path.insert(0,'.'); import dablab_run as d; print(d.MEDIA)")
mkdir -p "$M"
ffmpeg -nostdin -loglevel error -y -f lavfi -i "sine=f=440:r=48000:d=8" -ac 2 -metadata title="Titel Eins" -metadata artist="Testband A" -c:a libmp3lame -b:a 128k "$M/pl_titel1.mp3"
ffmpeg -nostdin -loglevel error -y -f lavfi -i "sine=f=880:r=48000:d=8" -ac 2 -metadata title="Titel Zwei" -metadata artist="Testband B" -c:a libvorbis -q:a 4 "$M/pl_titel2.ogg"
printf '#EXTM3U\n#EXTINF:8,Testband A - Titel Eins\npl_titel1.mp3\n#EXTINF:8,Testband B - Titel Zwei\npl_titel2.ogg\n' > /tmp/testliste.m3u
python3 dablab_run.py playlist import /tmp/testliste.m3u testliste
python3 dablab_run.py playlist export testliste /tmp/testliste_export.m3u8 && cat /tmp/testliste_export.m3u8
