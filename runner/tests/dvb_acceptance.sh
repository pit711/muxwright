#!/bin/bash
# DVB-Abnahme (WSL): check aller Beispiele, YouTube-Download, UDP an nc -lu, ctl live, Modulatorweg.
# Aufruf: wsl -d dablab -u root -- bash /mnt/g/DEEPSEEK/plutoTX/dablab/runner/tests/dvb_acceptance.sh
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=/mnt/g/DEEPSEEK/plutoTX/dablab/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=/mnt/g/DEEPSEEK/plutoTX/dablab/runner
OUT=$R/tests/results_wsl
mkdir -p $OUT
cd $R
{
echo "# DVB-Abnahme $(date '+%F %T')  TSDuck $(tsversion 2>&1)  $(ffmpeg -version | head -1 | cut -d' ' -f1-3)  yt-dlp $(yt-dlp --version)"
echo; echo "## YouTube-Download (Big Buck Bunny, Blender Foundation, CC BY 3.0, <= 480p)"
nice -n 10 python3 dablab_run.py media youtube "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --max-height 480 --force 2>/tmp/yt.err | python3 -c "import sys,json;r=json.load(sys.stdin);print({k:r.get(k) for k in ('ok','file','title','duration_s','width','height','bytes','license','elapsed_s')})"
grep -c '"youtube"' /tmp/yt.err | xargs echo "Fortschrittszeilen:"; grep '"youtube"' /tmp/yt.err | tail -1 | cut -c1-260
echo; echo "## check (20 s je Konfiguration)"
for c in examples/dvbt_testbild_sd examples/dvbt_hd_h264 examples/dvbc_zwei_programme examples/dvbc_usb_345 examples/dvbt_playlist examples/dvbt_dashboard_format tests/dvb_youtube_bbb; do
  echo "### $c"
  nice -n 10 python3 dablab_run.py check --config $c.json --seconds 20 > /tmp/chk.json 2>/tmp/chk.err
  python3 tests/dvb_summarize.py /tmp/chk.json
done
echo; echo "## UDP an nc -lu"; bash tests/dvb_udp_test.sh examples/dvbc_zwei_programme.json 30
echo; echo "## ctl live"; bash tests/dvb_ctl_test.sh
echo; echo "## Modulatorweg"; bash tests/dvb_mod_test.sh
echo; echo "## Aufraeumen"; ps -eo pid,cmd | grep -E "tsp --bitrate|dvb[tc]_mod -i|dablab_run.py live" | grep -v grep || echo "keine Restprozesse"
} 2>&1 | grep -v buffer_double_mapped | tee $OUT/dvb_acceptance.txt
