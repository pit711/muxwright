# DAB-Lab Pluto-Firmware (`pluto_fw/`)

Eigene Firmware für den ADALM-Pluto Rev. B (Zynq 7010, AD9364-Freischaltung) des Labor-Testsenders.
Basis ist **plutosdr-fw v0.31** – dieselbe Generation wie die bisher installierte Firmware
(`v0.31-4-g9ceb-dirty`, F5OEO-Build vom 05.02.2020): Linux 4.14 (ADI-Commit 387d584, identisch mit dem
Gerätekernel), ADI-Buildroot 2019.02.2, derselbe FPGA-Bitstream wie im Gerät. Neu sind libiio/iiod 0.25,
`dablab-agent`, `iqplay`, NE10, iperf3, Echtzeit-/DMA-Tuning und die Einbindung der On-Device-Modulatoren.

## Versionswahl und Begründung

| Komponente | Gerät (vorher) | diese Firmware | Begründung |
|---|---|---|---|
| plutosdr-fw | v0.31-4-g9ceb (F5OEO) | v0.31 + eigene Pakete | gleiche Generation → kein Treiber-/Bitstream-Risiko |
| Kernel | 4.14.0-g387d584d434e-dirty | 4.14.0-dablab-g387d584 (gleicher Commit, 2 Änderungen) | DMA-/IIO-Treiber zum Bitstream passend |
| FPGA | fpga@1 aus mtd3, md5 `36b9ff27…` | **identisch** (aus Backup entnommen) | nicht selbst baubar (kein Vivado); offizielles v0.31-Bit (`fpga/system_top_v0.31_official.bit`, md5 `660ad219…`) liegt als Alternative bei |
| U-Boot / Umgebung | v0.20-PlutoSDR (2017), Env mit `attr_name=compatible attr_val=ad9364` | **unverändert** (mtd0/mtd1 werden nie angefasst) | AD9364-Hack und DFU-Einträge bleiben |
| Buildroot | 2019.02.2 | 2019.02.2 (ADI-Fork b31cc4d) + BR2_EXTERNAL `br2ext/` | |
| Toolchain | Linaro/Xilinx gcc 7 | Bootlin armv7-eabihf glibc stable 2018.11-1 (gcc 7.3.0, glibc 2.27, Header 4.1) | Ubuntu-gcc 13 scheitert an Buildroot-2019 (Versionsprüfung, Debian-Sysroot-Layout); Linaro-Downloads sind offline; Bootlin-Toolchain entspricht der damaligen Generation |
| libiio/iiod | 0.18 | **0.25** (= PC-Client) | gleiche Protokollversion wie `pc/libiio` |
| libad9361-iio | 0.2 | 0.2 | `ad9361_set_bb_rate()` |

Der Kernel ist gegenüber v0.31 nur in zwei Punkten geändert (siehe `build.sh kernel`):
`industrialio_buffer_dma.max_block_size` Standard 64 MiB (statt 16 MiB) und `CONFIG_LOCALVERSION="-dablab"`
(Host-Fix für dtc/gcc≥10 ändert nichts am Zielcode). SMP (beide A9-Kerne), PREEMPT, HZ=100, CMA 256 MiB,
`CONFIG_RT_GROUP_SCHED` aus, kein cpufreq (CPU läuft fest mit 666,67 MHz – per `clk_summary`/SLCR gemessen).

## Architektur auf dem Gerät

```
PC (server.py) ──USB iiod (usb:) / ip:192.168.2.1 ──► iiod 0.25 (Kern 1, taskset)  ──► DAC   (DAB/UKW-Streaming wie bisher)
PC/WSL  ──UDP TS (bis 32 Mbit/s) über RNDIS/NCM ─────► dvbt_mod / dvbc_mod (Kern 1, SCHED_FIFO) ─libiio local─► DMA ─► DAC
PC  ──HTTP/JSON :8080 ───────────────────────────────► dablab-agent (Start/Stopp/Status/Rate/FIR/RF; iiod ↔ Kern 0 beim Modulatorbetrieb)
```

Init-Reihenfolge (Buildroot SysV): `S21misc` (xo_correction, max_block_size aus U-Boot `iio_max_block_size`),
**`S22dablab`** (sched_rt_runtime_us=-1, TX/RX-DMA-IRQs auf Kern 1, USB-IRQ auf Kern 0, UDP-Puffer 32 MiB,
max_block_size ≥ 64 MiB), `S23udc` (USB-Gadget RNDIS|NCM + ACM + MSD + iio_ffs, iiod mit `taskset`-Maske aus
U-Boot `iiod_cpu`, Standard 2 = Kern 1), … , **`S95dablab-agent`**.

U-Boot-Variablen (optional, per `fw_setenv`; alle haben Standardwerte, nichts muss gesetzt werden):
`iiod_cpu` (Maske 1/2/3), `usb_net` (`rndis` Standard | `ncm` | `ecm`), `dablab_port` (8080), `iio_max_block_size`.

## Verzeichnis

```
pluto_fw/
  build.sh            Build unter WSL „dablab“ (/opt/dablab/fw), Ergebnisse → out/
  boot_ram.cmd        Windows: Image per DFU in den RAM booten (Flash bleibt unberührt)
  br2ext/             BR2_EXTERNAL: configs/dablab.fragment, post-build.sh, overlay (S22dablab, S95dablab-agent),
                      package/{dablab-agent,iqplay,dablab-mod}, patches/ (Host-Fixes fakeroot/m4), local.mk (libiio 0.25)
  agent/              dablab-agent.c, Makefile, API.md  (Geräte-Dienst, API für den PC-Server)
  iqplay/             iqplay.c (IQ-Datei/UDP/Nullen/Sinus lokal in den DAC, Underflow-Zähler)
  mod/                dvbmod_lab (Modulator-Agent): Makefile im Ordner; Paket dablab-mod baut mit HAVE_LIBIIO=1, NEON, -O3
                      und installiert dvbt_mod, dvbc_mod, rrc_fir_gen, dvbmod_selftest nach /usr/bin
  fpga/               system_top_v0.31_official.bit (offizielles Release; ein aus dem eigenen Gerät gesicherter Bitstream
                      kann als fpga/system_top_mtd3.bit daneben gelegt werden und wird dann bevorzugt)
  backup/             mtd0..3 Sicherung vom 23.10.2026, pluto_info.txt, mtd3_fit_backup.dfu (= alte Firmware als RAM-Boot-Image)
  tools/dfu-util-win64/  dfu-util 0.11 für Windows
  test/device_tests.sh   Gerätetests (WSL): basic|agent|rate|iqplay|dma|udp
  out/                pluto.frm, pluto.dfu, pluto.itb, SHA256SUMS, VERSION, buildroot.config, linux.config
  MESSPROTOKOLL.md    Messungen/Nachweise
```

## Bauen (WSL1 „dablab“, Ubuntu 24.04)

```
wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/pluto_fw/build.sh all
#   Schritte einzeln: prepare | kernel | rootfs | image ; clean-rootfs löscht buildroot/output
```

`prepare` klont plutosdr-fw (Tag v0.31, Submodule buildroot+linux flach), lädt die Bootlin-Toolchain und libiio v0.25,
spiegelt `br2ext/agent/iqplay/mod` nach `/opt/dablab/fw`, setzt die Host-Fixes (fakeroot im TCP-Modus – WSL1 hat kein
SysV-IPC; `_STAT_VER`-Patch für glibc ≥ 2.33; m4-SIGSTKSZ-Patch; busybox `taskset`). `kernel` baut zImage + 3 DTBs
(8 Jobs, `nice 10`), `rootfs` erzeugt `rootfs.cpio.gz` (5,4 MiB), `image` setzt mit dem System-`mkimage` das FIT nach
`scripts/pluto.its` zusammen und erzeugt `pluto.frm` (FIT + md5) und `pluto.dfu` (FIT + DFU-Suffix 0456:b673).
Host-Pakete: `build-essential libncurses-dev unzip bc cpio rsync file wget python-is-python3 libssl-dev zip flex bison
gawk texinfo u-boot-tools dfu-util device-tree-compiler sshpass iperf3`. Komplettbuild ≈ 25 min (Kernel 3 min,
Buildroot 20 min) mit 8 Kernen. FIT-Größe 9,6 MiB (mtd3 = 30 MiB).

Versionsnachweis auf dem Gerät: `/opt/VERSIONS` (`device-fw v0.31-dablab.JJJJMMTT`, erscheint als iiod-Kontextattribut
`fw_version`, also in `iio_info`), `uname -r` = `4.14.0-dablab-g387d584d434e-dirty`.

## Test ohne Flashen: RAM-Boot per DFU

Nachgewiesener Ablauf (mit dem Backup-Image `backup/mtd3_fit_backup.dfu` durchgeführt, Gerät kam mit
`/proc/cmdline … earlyprintk` = `ramboot_verbose` hoch):

1. `ssh root@192.168.2.1 /usr/sbin/device_reboot ram` – setzt Reset-Ursache 7, U-Boot führt `ramboot_verbose`
   → `dfu_ram` aus und wartet als USB-Gerät 0456:b674 („USB download gadget“, Windows bindet WinUSB automatisch, kein Zadig nötig).
2. `tools\dfu-util-win64\dfu-util.exe -d 0456:b673,0456:b674 -a firmware.dfu -D out\pluto.dfu` (≈ 25 s für 10 MiB).
   Wichtig: `-d` mit beiden IDs angeben, sonst meckert dfu-util über den Suffix bzw. findet das ESP32-DFU-Gerät am PC.
3. `dfu-util.exe -d 0456:b673,0456:b674 -a firmware.dfu -e` (Detach) → U-Boot bootet das FIT aus dem RAM
   (`bootm 0x2080000#config@N`, N aus `adi_hwref` = Rev. B). Der Flash wird dabei nicht beschrieben.
4. Nach ≈ 30 s: `iio_info -u ip:192.168.2.1 | findstr fw_version`, `curl http://192.168.2.1:8080/api/status`.

Alles zusammen: `boot_ram.cmd [out\pluto.dfu]`. **Zurück zur Flash-Firmware: Strom/USB trennen** (oder
`device_reboot reset`) – das RAM-Image ist dann weg. Wenn ein RAM-Image nicht bootet (kein USB-Gerät erscheint), hilft
nur ein Aus-/Einstecken des USB-Kabels; U-Boot hat keinen Watchdog-Rückfall, und die serielle Konsole COM33 ist das
ACM-Gadget des Linux (in U-Boot nicht vorhanden).

## Stand

**Geflasht am 23.10.2026 13:06: Build 3 `v0.31-dablab.20261023-b3`** (pluto.frm SHA256 `76a35113…`), nach RAM-Test und mit
Freigabe. Geräteumgebung unverändert bis auf `fit_size` (9A8D53). Nachweise in `MESSPROTOKOLL.md` §5–6.

**Vorbereitet: Build 4 `v0.31-dablab.20261023-b4`** (`out/pluto.frm` 819de8e8…) – PAL/zyklische Dateien über den Agent,
Upload-Endpunkt, Modulator-Statusdurchreichung; RAM-Test ausstehend. Der geflashte Build 3 liegt in `out/build3/`.

## Flashen (erst nach bestandenem RAM-Test)

Weg A (Massenspeicher): `pluto.frm` auf das Laufwerk „PlutoSDR“ kopieren, Laufwerk auswerfen; das Gerät prüft Magic
(`ITB PlutoSDR (ADALM-PLUTO)`) und md5, schreibt mtd3 und startet neu (LED blinkt schnell – nicht trennen).
Weg B (DFU): `device_reboot sf` → `dfu-util -d 0456:b673,0456:b674 -a firmware.dfu -D out\pluto.dfu` → `-e`.
U-Boot-Umgebung (mtd1) bleibt in beiden Fällen unangetastet (`fw_printenv` vorher/nachher vergleichen).

## Recovery (zurück zum Backup)

- RAM-Boot verändert nichts: Kabel ziehen genügt.
- Zurück zur Werksfirmware nach dem Flash (Weg A, Massenspeicher, ohne DFU): `backup\werk_v0.31-4-g9ceb.frm`
  (= Backup-FIT aus mtd3 + md5-Anhang, Magic „ITB PlutoSDR (ADALM-PLUTO)“ geprüft) aufs Laufwerk „PlutoSDR“ kopieren,
  auswerfen, warten bis die LED wieder normal blinkt; update.sh setzt `fit_size` wieder auf 1AB13A3.
- Nach einem Flash per DFU: `device_reboot sf` (oder bei totem Linux: Taste beim Einstecken gedrückt halten → DFU),
  dann `dfu-util -d 0456:b673,0456:b674 -a firmware.dfu -D backup\mtd3_fit_backup.dfu` und `-e`.
  `mtd3_fit_backup.dfu` ist das alte FIT (mtd3, `fit_size` 0x1AB13A3 = 27 988 899 Byte) mit DFU-Suffix – mit genau
  dieser Datei wurde der RAM-Boot geprobt, sie bootet also nachweislich.
- Nur im Notfall U-Boot/Env: `dfu-util -a boot.dfu -D …` bzw. `uboot-env.dfu` – nicht nötig, wir ändern mtd0/mtd1 nie.
  Ein Zurückschreiben von `uboot-env.dfu` würde `attr_name/attr_val` (AD9364-Hack) und `ethaddr` überschreiben;
  danach `fw_setenv attr_name compatible; fw_setenv attr_val ad9364` setzen (Werte in `backup/pluto_info.txt`).
- Vollsicherung aller Partitionen: `backup/mtd0..3_*.bin` (dd aus `/dev/mtdN`).

## Agent und Modulatoren

API in `agent/API.md`. Kurz: `GET /api/status`, `POST /api/mod/start {"cmd","args",cpu,rt_prio}`, `/api/mod/stop`,
`/api/mod/log`, `POST /api/rate {"rate_hz"} | {"rate_hz","taps"[16·n],"interp","dec"} | {"fir_en"}`, `POST /api/rf`,
`POST /api/iiod {"cpu","nice"}`, `/api/underflow/clear`, `POST /api/reboot {"mode":"ram"|"sf"|"reset"}`.
Underflow-Zähler: Register 0x88 Bit 0 des DDS-Cores (sticky, W1C), alle 200 ms gepollt.

`iqplay -r RATE [-f datei|-z|-s ton|-u port] [-c] [-t s] [-l LO] [-g dB] …` – Beispiele in `iqplay/iqplay.c`.
Modulatoren: `mod/` (Makefile; CFLAGS/LDFLAGS gehen als Umgebung hinein, sonst ginge `-DHAVE_LIBIIO` verloren); nach
`build.sh rootfs image` liegen sie in `/usr/bin`. Start über den Agent z. B.
`{"cmd":"/usr/bin/dvbt_mod","args":["-i","udp://:5000","-o","iio:","--mode","8k","--const","64qam","--rate","2/3","--guard","1/32","--bw","8","--lo","594000000","--affinity","--rt"],"cpu":-1,"rt_prio":0}`
(dvbt_mod verteilt seine Threads selbst; Pegel bleibt −89,75 dB bis `--enable --atten X` bzw. `POST /api/rf`).
Stumme Inhaltsprüfung: `test/dvbt_loopback_check.sh` (AD9361 digitaler Loopback → gr-dtv → `test/ts_align_compare.py`). Zum schnellen Testen ohne Neubau: Binary per scp nach `/tmp` und per
Agent mit `"cmd":"/tmp/dvbt_mod"` starten.
