#!/bin/bash
# Sol Levante (Netflix Open Content, CC BY 4.0), HDR10-ProRes per HTTP-Range → HEVC Main10 2160p25 HDR10 ~11 Mbit/s + AAC-LC 192k
# Mastering: P3-D65 in BT.2020, 1000 nit (laut Dateiname; die MOV enthält keine mdcv/clli-Angaben) → master-display/max-cll Standardwerte
W=/opt/dablab/mod/stream; cd $W
U="http://download.opencontent.netflix.com.s3.amazonaws.com/SolLevante/hdr10/SolLevante_HDR10_r2020_ST2084_UHD_24fps_1000nit.mov"
SS=${SS:-60}; T=${T:-45}; VBR=${VBR:-11000}
nice -n 10 ffmpeg -hide_banner -loglevel warning -stats -y -ss $SS -t $T -i "$U" -map 0:v:0 -map 0:a:1 \
  -vf "fps=25,format=yuv420p10le" -color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -color_range tv \
  -c:v libx265 -preset veryfast -profile:v main10 \
  -x265-params "pools=2:frame-threads=1:vbv-maxrate=$VBR:vbv-bufsize=$((VBR/2)):bitrate=$((VBR-300)):keyint=25:min-keyint=25:scenecut=0:repeat-headers=1:aud=1:hrd=1:hdr10=1:hdr10-opt=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50):max-cll=1000,400:log-level=error" \
  -c:a aac -b:a 192k -ar 48000 -ac 2 -f mpegts hdr_hevc.ts 2>&1 | tr '\r' '\n' | grep -E "frame=" | tail -1
ls -la hdr_hevc.ts
ffprobe -v error -show_entries stream=codec_name,profile,width,height,pix_fmt,color_transfer,color_primaries -of compact hdr_hevc.ts
