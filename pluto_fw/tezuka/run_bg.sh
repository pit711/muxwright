#!/bin/bash
# Build abgekoppelt starten: bash run_bg.sh [board]  → /root/mx_build.log, letzte Zeile EXIT=<rc>
cd "$(dirname "$0")"
bash ./build_tezuka.sh "${1:-fishball}" > /root/mx_build.log 2>&1
echo "EXIT=$?" >> /root/mx_build.log
