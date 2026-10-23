#!/bin/bash
# 4K-Ausschnitt → HEVC Main 2160p25 (~17 Mbit/s, VBV, GOP 1 s) + AAC-LC 192k, dann HD+UHD in einen Transponder-TS (FEC 3/4, 30 411 765 b/s)
W=/opt/dablab/mod/stream; cd $W
SRC=${SRC:-$W/bbb_sunflower_2160p_30fps_normal.mp4}; SS=${SS:-60}; T=${T:-120}
case "$1" in
encode)
  nice -n 10 ffmpeg -hide_banner -loglevel warning -stats -y -ss $SS -t $T -threads 4 -i "$SRC" -map 0:v:0 -map 0:a:0 \
    -vf "fps=25,format=yuv420p" -c:v libx265 -preset veryfast \
    -x265-params "pools=4:frame-threads=2:vbv-maxrate=${VBR:-11500}:vbv-bufsize=$(( ${VBR:-11500} / 2 )):bitrate=$(( ${VBR:-11500} - 300 )):keyint=25:min-keyint=25:scenecut=0:repeat-headers=1:aud=1:hrd=1:log-level=error" \
    -profile:v main -tag:v hvc1 -c:a aac -b:a 192k -ar 48000 -ac 2 -f mpegts uhd_hevc.ts 2>&1 | tr '\r' '\n' | grep -E "frame=" | tail -1
  ls -la uhd_hevc.ts
  ;;
mux)
  RATE=30411765
  nice -n 10 ffmpeg -hide_banner -loglevel error -y -t $T -i bbb_sat.ts -t $T -i uhd_hevc.ts -map 0:v -map 0:a -map 1:v -map 1:a -c copy \
    -program "program_num=1:title=DABLAB Sat:st=0:st=1" -program "program_num=2:title=DABLAB Sat UHD:st=2:st=3" \
    -mpegts_original_network_id 0xFF03 -mpegts_transport_stream_id 0x0003 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200 \
    -muxrate $RATE -pcr_period 30 -f mpegts both_raw.ts
  tsp -I file both_raw.ts -P sdt --service 1 --name "DABLAB Sat" --provider DABLAB --type 0x19 \
                          -P sdt --service 2 --name "DABLAB Sat UHD" --provider DABLAB --type 0x1F -O file sat_hd_uhd.ts 2>&1 | tail -2
  ls -la sat_hd_uhd.ts
  tsanalyze sat_hd_uhd.ts | grep -E "Service name|Service type|HEVC|AVC|AAC|Bitrate: \." | head -12
  ;;
mux3)
  RATE=35480392; T3=${T3:-45}
  nice -n 10 ffmpeg -hide_banner -loglevel error -y -t $T3 -i bbb_sat.ts -t $T3 -i uhd_hevc.ts -t $T3 -i hdr_hevc.ts     -map 0:v -map 0:a -map 1:v -map 1:a -map 2:v -map 2:a -c copy     -program "program_num=1:title=DABLAB Sat:st=0:st=1" -program "program_num=2:title=DABLAB Sat UHD:st=2:st=3" -program "program_num=3:title=DABLAB Sat HDR:st=4:st=5"     -mpegts_original_network_id 0xFF03 -mpegts_transport_stream_id 0x0003 -mpegts_pmt_start_pid 0x0100 -mpegts_start_pid 0x0200     -muxrate $RATE -pcr_period 30 -f mpegts three_raw.ts
  tsp -I file three_raw.ts -P sdt --service 1 --name "DABLAB Sat" --provider DABLAB --type 0x19                            -P sdt --service 2 --name "DABLAB Sat UHD" --provider DABLAB --type 0x1F                            -P sdt --service 3 --name "DABLAB Sat HDR" --provider DABLAB --type 0x1F -O file sat_3dienste.ts 2>&1 | tail -2
  ls -la sat_3dienste.ts
  tsanalyze sat_3dienste.ts | grep -E "Service name|Service type|HEVC|AVC|AAC|Average bitrate|TS bitrate" | head -16
  ;;
esac
