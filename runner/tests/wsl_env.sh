#!/bin/bash
# Umgebung fuer Tests unter WSL1 (dablab-Distribution). Aufruf: wsl -d dablab -- bash /mnt/g/.../tests/wsl_env.sh <befehl...>
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # Repo-Wurzel (dablab/)
export DABLAB_BIN=/opt/dablab/bin
export DABLAB_RUNTIME=$ROOT/runtime
export PATH=/opt/dablab/bin:/usr/local/bin:/usr/bin:/bin
cd $ROOT/runner
exec "$@"
