#!/bin/bash
for p in yt-dlp gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf u-boot-tools device-tree-compiler libssl-dev bc bison flex libncurses-dev rsync cpio unzip wget python3-pip; do
  printf "%-28s %s\n" "$p" "$(apt-cache policy $p 2>/dev/null | awk '/Candidate/{print $2}')"
done
