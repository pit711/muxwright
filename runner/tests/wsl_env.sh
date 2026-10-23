#!/bin/bash
# Umgebung fuer Tests unter WSL1 (dablab-Distribution). Aufruf: wsl -d dablab -- bash /mnt/g/.../tests/wsl_env.sh <befehl...>
export DABLAB_BIN=/opt/dablab/bin
export DABLAB_RUNTIME=/mnt/g/DEEPSEEK/plutoTX/dablab/runtime
export PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
cd /mnt/g/DEEPSEEK/plutoTX/dablab/runner
exec "$@"
