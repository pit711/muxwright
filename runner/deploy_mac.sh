#!/bin/bash
# Nur fuer Tests auf dem Mac: Runner nach ~/dablab/runtime/ kopieren (am PC/WSL laeuft er direkt aus dem G:-Ordner).
set -e
HOST=${1:-${DABLAB_SSH_HOST:?Ziel user@host angeben}}
D=$(cd "$(dirname "$0")" && pwd)
ssh -o BatchMode=yes "$HOST" 'mkdir -p ~/dablab/runtime/media ~/dablab/runtime/examples ~/dablab/runtime/tests ~/dablab/runtime/patches'
scp -q "$D/dablab_run.py" "$HOST:dablab/runtime/"
scp -q "$D"/examples/*.json "$HOST:dablab/runtime/examples/" 2>/dev/null || true
scp -q "$D"/tests/*.py "$D"/tests/*.sh "$HOST:dablab/runtime/tests/" 2>/dev/null || true
scp -q "$D"/patches/*.py "$HOST:dablab/runtime/patches/" 2>/dev/null || true
echo "deployed to $HOST:~/dablab/runtime"
