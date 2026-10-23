#!/bin/bash
# DAB-Lab Pluto-Firmware – Build-Skript (WSL1 „dablab“, Ubuntu 24.04)
#
#   wsl -d dablab -u root -- bash /mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw/build.sh [all|prepare|kernel|rootfs|image|clean-rootfs]
#
# Baut unter /opt/dablab/fw (schnelles WSL-Dateisystem) und kopiert Ergebnisse nach pluto_fw/out.
# Basis: plutosdr-fw v0.31 (Linux 4.14 @387d584, ADI-Buildroot 2019.02.2) – dieselbe Generation wie das Gerät.
# FPGA-Bitstream wird NICHT gebaut, sondern aus dem Backup (mtd3) bzw. dem offiziellen v0.31-Release übernommen.
set -eo pipefail

FW=/opt/dablab/fw
SRC=$FW/plutosdr-fw
G=${DABLAB_PLUTO_FW:-/mnt/g/DEEPSEEK/plutoTX/dablab/pluto_fw}
TCDIR=$FW/toolchain/armv7-eabihf--glibc--stable-2018.11-1
TCURL=https://toolchains.bootlin.com/downloads/releases/toolchains/armv7-eabihf/tarballs/armv7-eabihf--glibc--stable-2018.11-1.tar.bz2
CROSS=arm-buildroot-linux-gnueabihf-
JOBS=${JOBS:-8}
NICE="nice -n 10"
VERSION=${VERSION:-v0.31-dablab.$(date +%Y%m%d)}
LOGDIR=$FW/logs
export PATH=$TCDIR/bin:$PATH
mkdir -p $FW $LOGDIR $FW/build $FW/dl

log() { echo "=== [$(date +%H:%M:%S)] $*"; }

prepare() {
	log "prepare"
	if [ ! -d $SRC/.git ]; then
		git clone --depth 1 -b v0.31 https://github.com/analogdevicesinc/plutosdr-fw.git $SRC
	fi
	cd $SRC
	git fetch -q --depth 1 origin tag v0.31 2>/dev/null || true
	git checkout -q v0.31 2>/dev/null || true
	git submodule sync -q
	[ -f buildroot/Makefile ] || git submodule update --init --depth 1 buildroot
	[ -f linux/Makefile ] || git submodule update --init --depth 1 linux
	if [ ! -x $TCDIR/bin/${CROSS}gcc ]; then
		mkdir -p $FW/toolchain && cd $FW/toolchain
		[ -f tc.tar.bz2 ] || wget -q -O tc.tar.bz2 $TCURL
		tar xjf tc.tar.bz2
	fi
	if [ ! -d $FW/dl/libiio-0.25 ]; then
		git clone -q --depth 1 -b v0.25 https://github.com/analogdevicesinc/libiio.git $FW/dl/libiio-0.25
	fi
	# Quellen vom langsamen G: nach /opt spiegeln
	rsync -a --delete $G/br2ext/ $FW/br2ext/
	rsync -a --delete $G/agent/ $FW/agent/
	rsync -a --delete $G/iqplay/ $FW/iqplay/
	if [ -d $G/mod ]; then rsync -a --delete --exclude 'tests/results' --exclude '*.o' --exclude '*.a' $G/mod/ $FW/mod/; else rm -rf $FW/mod; fi
	mkdir -p $FW/br2ext/patches
	# WSL1 hat kein SysV-IPC: host-fakeroot muss den TCP-Modus benutzen
	FRMK=$SRC/buildroot/package/fakeroot/fakeroot.mk
	grep -q 'with-ipc=tcp' $FRMK || echo 'HOST_FAKEROOT_CONF_OPTS += --with-ipc=tcp' >> $FRMK
	# Busybox: taskset (mit -c) für Kernzuordnung von iiod/Modulator
	BBCFG=$SRC/buildroot/board/pluto/busybox-1.25.0.config
	if ! grep -q '^CONFIG_TASKSET=y' $BBCFG; then
		sed -i 's/^# CONFIG_TASKSET is not set/CONFIG_TASKSET=y/; s/^# CONFIG_FEATURE_TASKSET_FANCY is not set/CONFIG_FEATURE_TASKSET_FANCY=y/' $BBCFG
		grep -q '^CONFIG_TASKSET=y' $BBCFG || printf 'CONFIG_TASKSET=y\nCONFIG_FEATURE_TASKSET_FANCY=y\n' >> $BBCFG
		rm -rf $SRC/buildroot/output/build/busybox-*
	fi
	# FPGA-Bitstream: Backup (mtd3) bevorzugt, sonst offizielles Release v0.31
	mkdir -p $FW/fpga
	if [ -f $G/fpga/system_top_mtd3.bit ]; then cp $G/fpga/system_top_mtd3.bit $FW/fpga/system_top.bit
	elif [ -f $G/backup/mtd3_linux_2026-10-23.bin ]; then
		dumpimage -T flat_dt -p 3 -o $FW/fpga/system_top.bit $G/backup/mtd3_linux_2026-10-23.bin >/dev/null
	else
		[ -f $FW/dl/system_top_v0.31.hdf ] || wget -q -O $FW/dl/system_top_v0.31.hdf https://github.com/analogdevicesinc/plutosdr-fw/releases/download/v0.31/system_top.hdf
		unzip -o -q $FW/dl/system_top_v0.31.hdf system_top.bit -d $FW/fpga
	fi
	md5sum $FW/fpga/system_top.bit
}

kernel() {
	log "kernel"
	cd $SRC/linux
	# Host-Fix: gcc>=10 (-fno-common) -> doppelte Definition yylloc in scripts/dtc
	sed -i 's/^YYLTYPE yylloc;/extern YYLTYPE yylloc;/' scripts/dtc/dtc-lexer.lex.c_shipped scripts/dtc/dtc-lexer.l
	# IIO-DMA: Standard-Blockgrenze 64 MiB statt 16 MiB (zusätzlich per Init-Skript setzbar)
	sed -i 's/iio_dma_buffer_max_block_size = SZ_16M/iio_dma_buffer_max_block_size = SZ_64M/' drivers/iio/buffer/industrialio-buffer-dma.c
	make ARCH=arm zynq_pluto_defconfig >/dev/null
	./scripts/config --set-str LOCALVERSION "-dablab" --disable RT_GROUP_SCHED --disable CPU_FREQ --enable PREEMPT --enable SMP
	make ARCH=arm olddefconfig >/dev/null
	$NICE make -j$JOBS ARCH=arm CROSS_COMPILE=$CROSS zImage zynq-pluto-sdr.dtb zynq-pluto-sdr-revb.dtb zynq-pluto-sdr-revc.dtb
	cp arch/arm/boot/zImage $FW/build/
	cp arch/arm/boot/dts/zynq-pluto-sdr.dtb arch/arm/boot/dts/zynq-pluto-sdr-revb.dtb arch/arm/boot/dts/zynq-pluto-sdr-revc.dtb $FW/build/
	ls -la $FW/build/zImage $FW/build/*.dtb
}

rootfs() {
	log "rootfs (buildroot)"
	cd $SRC
	# VERSIONS wie im ADI-Makefile (wird zu /opt/VERSIONS und zur iiod-Kontextvariablen fw_version)
	{
		echo "device-fw $VERSION"
		# hdl/u-boot werden nicht gebaut: Stand laut Tag v0.31 des Superprojekts
		echo "hdl $(git ls-tree v0.31 hdl | awk '{print substr($3,1,5)}') (v0.31, Bitstream aus Geraete-Backup)"
		echo "buildroot $(cd buildroot && git describe --abbrev=4 --dirty --always --tags 2>/dev/null)"
		echo "linux $(cd linux && git describe --abbrev=4 --dirty --always --tags 2>/dev/null)"
		echo "u-boot-xlnx $(git ls-tree v0.31 u-boot-xlnx | awk '{print substr($3,1,5)}') (v0.31, auf dem Geraet: v0.20-PlutoSDR, unveraendert)"
		echo "toolchain bootlin-armv7-eabihf-glibc-stable-2018.11-1 (gcc 7.3.0)"
		echo "libiio v0.25"
		echo "fpga mtd3-backup md5=$(md5sum $FW/fpga/system_top.bit | cut -c1-32)"
	} > buildroot/board/pluto/VERSIONS
	cat buildroot/board/pluto/VERSIONS
	cd buildroot
	mkdir -p output
	make BR2_EXTERNAL=$FW/br2ext zynq_pluto_defconfig >/dev/null
	FRAG=$FW/br2ext/configs/dablab.fragment
	if [ -f $FW/mod/Makefile ]; then
		cp $FRAG /tmp/dablab.fragment; echo "BR2_PACKAGE_DABLAB_MOD=y" >> /tmp/dablab.fragment; FRAG=/tmp/dablab.fragment
	fi
	./support/kconfig/merge_config.sh -m .config $FRAG >/dev/null
	make olddefconfig >/dev/null
	grep -E "^BR2_(TOOLCHAIN_EXTERNAL_(PATH|CUSTOM_PREFIX|HEADERS_4_1|GCC_7)|PACKAGE_(NE10|IPERF3|DABLAB_AGENT|IQPLAY|DABLAB_MOD)|JLEVEL|ROOTFS_OVERLAY|GLOBAL_PATCH_DIR)" .config
	# Lizenzseite für das Massenspeicher-Laufwerk (best effort wie im ADI-Makefile)
	if [ ! -f board/pluto/msd/LICENSE.html ]; then
		( $NICE make legal-info > $LOGDIR/legal-info.log 2>&1 && cd $SRC && bash scripts/legal_info_html.sh "PlutoSDR" "$SRC/buildroot/board/pluto/VERSIONS" && cp build/LICENSE.html buildroot/board/pluto/msd/LICENSE.html ) \
		|| { echo "<html><body><h1>PlutoSDR DAB-Lab $VERSION</h1><p>Open-Source-Lizenzen: siehe buildroot/output/legal-info im Build-Baum.</p></body></html>" > board/pluto/msd/LICENSE.html; }
	fi
	# lokale Pakete (SITE_METHOD=local) werden von Buildroot nur einmal synchronisiert → jedes Mal frisch übernehmen
	for pk in dablab-agent iqplay dablab-mod; do make $pk-dirclean >/dev/null 2>&1 || true; done
	$NICE make ARCH=arm CROSS_COMPILE=$CROSS BUSYBOX_CONFIG_FILE=$SRC/buildroot/board/pluto/busybox-1.25.0.config all
	cp output/images/rootfs.cpio.gz $FW/build/
	ls -la $FW/build/rootfs.cpio.gz
}

image() {
	log "image (FIT, frm, dfu)"
	cd $FW/build
	cp $FW/fpga/system_top.bit system_top.bit
	sed -e 's#\.\./build/##' $SRC/scripts/pluto.its > pluto.its
	rm -f pluto.itb pluto.frm pluto.dfu
	mkimage -f pluto.its pluto.itb >/dev/null
	md5sum pluto.itb | cut -d ' ' -f 1 > pluto.itb.md5
	cat pluto.itb pluto.itb.md5 > pluto.frm
	cp pluto.itb pluto.dfu.tmp && dfu-suffix -a pluto.dfu.tmp -v 0x0456 -p 0xb673 >/dev/null && mv pluto.dfu.tmp pluto.dfu
	ls -la pluto.itb pluto.frm pluto.dfu
	SIZE=$(stat -c %s pluto.frm); echo "pluto.frm: $SIZE Byte ($((SIZE/1048576)) MiB von 30 MiB mtd3)"
	[ $SIZE -le $((0x1E00000)) ] || { echo "FEHLER: FIT passt nicht in mtd3 (30 MiB)"; exit 1; }
	dumpimage -l pluto.frm | grep -E "Image|Description|Data Size|Hash value" | head -40
	mkdir -p $G/out
	cp pluto.frm pluto.dfu pluto.itb $G/out/
	cp zImage $G/out/zImage; cp zynq-pluto-sdr-revb.dtb $G/out/
	cp $SRC/buildroot/board/pluto/VERSIONS $G/out/VERSIONS
	cp $SRC/buildroot/.config $G/out/buildroot.config; cp $SRC/linux/.config $G/out/linux.config
	( cd $G/out && sha256sum pluto.frm pluto.dfu pluto.itb > SHA256SUMS && cat SHA256SUMS )
	echo "$VERSION" > $G/out/VERSION
}

case "${1:-all}" in
	prepare) prepare ;;
	kernel) prepare; kernel ;;
	rootfs) prepare; rootfs ;;
	image) image ;;
	clean-rootfs) rm -rf $SRC/buildroot/output ;;
	all) prepare; kernel; rootfs; image ;;
	*) echo "usage: $0 [all|prepare|kernel|rootfs|image|clean-rootfs]"; exit 2 ;;
esac
log "fertig: $1"
