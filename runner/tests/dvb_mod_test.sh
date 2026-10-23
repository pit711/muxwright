#!/bin/bash
# PC-Streaming-Weg: Runner -> TS -> dvbt_mod/dvbc_mod -> IQ int16 (stdout). Rendert IQ, demoduliert mit den
# Pruefempfaengern aus pluto_fw/mod/tests (gr-dtv bzw. numpy) zurueck zu TS und prueft den TS mit tsanalyze/ffprobe.
# Danach: live mit Echtzeit-Leser auf der Abtastrate (Durchsatzreserve).
# Aufruf (WSL): bash runner/tests/dvb_mod_test.sh
export DABLAB_BIN=/opt/dablab/bin DABLAB_RUNTIME=/mnt/g/DEEPSEEK/plutoTX/dablab/runtime PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
R=/mnt/g/DEEPSEEK/plutoTX/dablab/runner
M=/mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/mod/tests
cd $R
W=/tmp/dvbmodtest; mkdir -p $W

echo "=== DVB-T 6 MHz 8k 64-QAM 2/3 1/32 (dvbt_hd_h264), 6 s IQ"
nice -n 10 python3 dablab_run.py render --config examples/dvbt_hd_h264.json --seconds 6 --out $W/t.iq --format s16 2>$W/t.err | python3 -c "import sys,json;r=json.load(sys.stdin);print({k:r[k] for k in ('ok','bytes','elapsed_s')}, 'samples', r['bytes']//4, 'signal_s', round(r['bytes']/4/6857142.857,3))"
grep -E '"lvl": "(warn|error)"' $W/t.err | head -3 | cut -c1-250
nice -n 10 python3 $M/dvbt_rx_grdtv.py --iq $W/t.iq --mode 8k --const 64qam --rate 2/3 --guard 1/32 -o $W/t_rx.ts 2>&1 | tail -2
ls -la $W/t_rx.ts
tsanalyze $W/t_rx.ts 2>/dev/null | grep -E "Services:|TS packets|Transport stream bitrate|LAB HD|transport-errors|Invalid" | head -6
ffprobe -v error -show_entries stream=codec_name,width,height -of compact $W/t_rx.ts 2>&1 | head -4
ffmpeg -v error -i $W/t_rx.ts -map 0:v -f null - 2>&1 | wc -l | xargs echo "ffmpeg-Fehlerzeilen Video-Decode:"

echo; echo "=== DVB-C 3,45 MBd 256-QAM (dvbc_usb_345), 4 s IQ"
nice -n 10 python3 dablab_run.py render --config examples/dvbc_usb_345.json --seconds 4 --out $W/c.iq --format s16 2>$W/c.err | python3 -c "import sys,json;r=json.load(sys.stdin);print({k:r[k] for k in ('ok','bytes','elapsed_s')}, 'samples', r['bytes']//4, 'signal_s', round(r['bytes']/4/6.9e6,3))"
grep -E '"lvl": "(warn|error)"' $W/c.err | head -3 | cut -c1-250
nice -n 10 python3 $M/dvbc_demod.py --iq $W/c.iq --qam 256 --sps 2 --sr 3.45e6 --max-symbols 4000000 --json $W/c_rx.json 2>&1 | tail -3 | cut -c1-400
ls -la $W/*.ts 2>/dev/null

echo; echo "=== live mit Modulator, Echtzeit-Leser 20 s (DVB-T 6 MHz: 27,43 MB/s)"
nice -n 10 python3 dablab_run.py live --config examples/dvbt_hd_h264.json 2>$W/live.err | python3 tests/dvb_reader.py --rate $((6857143*32)) --seconds 20
grep '"status"' $W/live.err | tail -1 | cut -c1-300
echo "=== live mit Modulator ungebremst 10 s"
nice -n 10 python3 dablab_run.py live --config examples/dvbt_hd_h264.json 2>/dev/null | python3 tests/dvb_reader.py --rate $((6857143*32)) --seconds 10 --unpaced
echo "=== DVB-C live ungebremst 10 s (Soll 6,9 MS/s = 220,8 Mb/s)"
nice -n 10 python3 dablab_run.py live --config examples/dvbc_usb_345.json 2>/dev/null | python3 tests/dvb_reader.py --rate $((6900000*32)) --seconds 10 --unpaced
ps -eo pid,cmd | grep -E "dvb[tc]_mod|tsp --bitrate" | grep -v grep | head -3
