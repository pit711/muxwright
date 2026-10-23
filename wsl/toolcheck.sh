#!/bin/bash
# Prüft die Werkzeuge in der WSL-Distribution „dablab“
export PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
odr-dabmux -V 2>&1 | head -1
odr-dabmod -h 2>&1 | head -1
odr-audioenc --version 2>&1 | head -1
odr-padenc --version 2>&1 | head -1
welle-cli -h 2>&1 | head -1
command -v ffmpeg convert magick python3
python3 -c "import numpy,scipy;print('numpy',numpy.__version__,'scipy',scipy.__version__)"
id -un
