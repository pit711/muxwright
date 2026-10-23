#!/bin/bash
# UKW über den Runner (WSL) → iio_writedev (libiio, Netzwerk-Backend) → Pluto-DAC. start [CONFIG] | stop | status
R=/mnt/g/DEEPSEEK/plutoTX/dablab/runner; L=/opt/dablab/mod/stream/fm.log
case "$1" in
start)
  CFG=${2:-$R/examples/fm_standard.json}
  cd $R
  setsid nohup bash -c "python3 dablab_run.py live --config $CFG 2>$L.runner | iio_writedev -u ip:192.168.2.1 -b 262144 cf-ad9361-dds-core-lpc voltage0 voltage1" > $L 2>&1 &
  sleep 5; echo "gestartet: runner $(pgrep -f 'dablab_run.py live' | head -1) iio_writedev $(pgrep -f 'iio_writedev -u ip:192.168.2.1' | head -1)"
  ;;
stop) pkill -f "iio_writedev -u ip:192.168.2.1"; pkill -f "dablab_run.py live"; echo gestoppt ;;
status) echo "runner: $(pgrep -c -f 'dablab_run.py live') iio_writedev: $(pgrep -c -f 'iio_writedev -u ip:')"; tail -2 $L; grep -E '"rate|status|amplitudes' $L.runner | tail -1 | cut -c1-300 ;;
esac
