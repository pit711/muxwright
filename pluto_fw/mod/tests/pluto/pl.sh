#!/bin/bash
# Pluto-Hilfe aus WSL: pl.sh push DATEI… (nach /tmp) | pl.sh run "BEFEHL" | pl.sh kill-stale
S="sshpass -p analog"; O="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5"
case "$1" in
push) shift; timeout 120 $S scp -O $O "$@" root@192.168.2.1:/tmp/ && timeout 20 $S ssh $O root@192.168.2.1 "cd /tmp && chmod +x $(for f in "$@"; do printf '%s ' "$(basename "$f")"; done)" ;;
run)  shift; $S ssh $O root@192.168.2.1 "$*" ;;
kill-stale) for p in $(pgrep -x scp) $(pgrep -f "ssh.*192.168.2.1 sftp"); do kill "$p"; done; echo ok ;;
esac
