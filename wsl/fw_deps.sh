#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
nice -n 10 apt-get install -y -qq gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf u-boot-tools device-tree-compiler libssl-dev bc bison flex libncurses-dev rsync cpio unzip wget python3-pip file patch perl gawk texinfo libelf-dev squashfs-tools mtd-utils dosfstools > /tmp/fw_deps.log 2>&1 && echo "fw deps ok" || tail -5 /tmp/fw_deps.log
nice -n 10 python3 -m pip install -q --break-system-packages -U yt-dlp > /dev/null 2>&1 && yt-dlp --version
