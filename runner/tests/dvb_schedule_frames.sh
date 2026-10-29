#!/bin/bash
# Bilder an bestimmten Bildnummern (n = Bilder seit Stromstart) aus einem TS ziehen und zu einem Raster zusammensetzen.
# Aufruf: dvb_schedule_frames.sh <ts> <ausgabe.png> n1 n2 ...   (je zwei Bilder bilden eine Zeile)
TS=$1; OUT=$2; shift 2
D=$(mktemp -d)
ffprobe -v error -select_streams v:0 -count_packets -show_entries stream=start_time,nb_read_packets -of compact "$TS"
ffprobe -v error -select_streams a:0 -show_entries stream=start_time -of compact "$TS"
ins=(); i=0
for n in "$@"; do
  ffmpeg -nostdin -loglevel error -i "$TS" -map 0:v:0 -vf "select=eq(n\,$n),scale=320:-2,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='n=$n':fontsize=20:fontcolor=white:box=1:boxcolor=red:x=4:y=150" -frames:v 1 -y $D/$i.png
  ins+=(-i $D/$i.png); i=$((i+1))
done
f=""; rows=""; r=0
for ((k=0; k<i; k+=2)); do f="$f[$k][$((k+1))]hstack[r$r];"; rows="$rows[r$r]"; r=$((r+1)); done
ffmpeg -loglevel error -y "${ins[@]}" -filter_complex "${f}${rows}vstack=$r" "$OUT"
rm -rf $D
