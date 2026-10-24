#!/bin/bash
# atsc_mod auf dem Pluto: Bitgleichheit ARM vs. x86 (md5 über 10 Felder) und Durchsatz je Tap-Zahl (nur CPU, -o /dev/null, nie iio:)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"; M=$ROOT/pluto_fw/mod
X86=$(MSYS_NO_PATHCONV=1 wsl -d dablab -u root -e bash -c "/mnt/${M:1:1}${M:2}/atsc_mod -i prbs:3120 -o - --quiet 2>/dev/null | md5sum" 2>/dev/null || $M/atsc_mod -i prbs:3120 -o - --quiet 2>/dev/null | md5sum)
echo "x86 md5: $X86"
cd $M/tests/pluto && python pluto_run.py put ../../../../pc/device_files/atsc_mod_iio >/dev/null 2>&1
python pluto_run.py run "cd /tmp; echo -n 'arm md5: '; ./atsc_mod_iio -i prbs:3120 -o - --quiet 2>/dev/null | md5sum; for t in ${TAPS:-9 65 81 97}; do echo -n \"taps \$t \"; ./atsc_mod_iio -i prbs -o /dev/null --seconds ${SECS:-2} --taps \$t \$EXTRA 2>&1 | tail -1; done" 600
