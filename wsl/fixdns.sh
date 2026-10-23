#!/bin/bash
# WSL1: DNS fest einstellen (Tailscale liefert sonst ungültige IPv6-Nameserver)
for p in $(pgrep -x apt-get) $(pgrep -f /usr/lib/apt/methods/); do kill "$p" 2>/dev/null; done
printf '[boot]\nsystemd=false\n[network]\ngenerateResolvConf = false\n' > /etc/wsl.conf
rm -f /etc/resolv.conf
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
timeout 10 getent hosts archive.ubuntu.com && echo DNS_OK
