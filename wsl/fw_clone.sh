#!/bin/bash
# plutosdr-fw (offiziell, Basis auch für F5OEO-Forks) flach klonen – läuft im Hintergrund
mkdir -p /opt/dablab/fw && cd /opt/dablab/fw
[ -d plutosdr-fw ] || nice -n 10 git clone -q --depth 1 --recurse-submodules --shallow-submodules https://github.com/analogdevicesinc/plutosdr-fw.git > /tmp/fw_clone.log 2>&1
cd plutosdr-fw && git log -1 --format='%h %cd %s' && git submodule status | cut -c1-80 && du -sh . && echo "clone fertig"
