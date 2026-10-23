# Messprotokoll Pluto-Firmware DAB-Lab (Stand 23.10.2026, 02:50)

Gerät: ADALM-Pluto Rev. B, U-Boot v0.20-PlutoSDR (2017), Env mit `attr_name=compatible attr_val=ad9364`, mtd3 30 MiB.

## 1. Bestandsaufnahme (alte Firmware, per SSH)
- `device-fw v0.31-4-g9ceb-dirty`, Linux 4.14.0-g387d584d434e-dirty #14 SMP PREEMPT (16.11.2019), Buildroot 2019.02.2.
- iiod/libiio 0.18, libad9361 0.2. Beide A9-Kerne aktiv (`maxcpus=` leer → Kernel nutzt alle), CPU-Takt 666,67 MHz
  (clk_summary: armpll 1333,33 MHz, cpu_div 666,67 MHz; SLCR ARM_CLK_CTRL 0x1F000200), kein cpufreq → immer Volltakt.
- `industrialio_buffer_dma/parameters/max_block_size` bereits 67108864 (S21misc), CMA 256 MiB.
- IRQs: 47 = 7c400000.dma (RX), 48 = 7c420000.dma (TX, 336 719 Interrupts), 44 = e0002000.usb – alle auf CPU0.
- `sched_rt_runtime_us` 950000, usb0 MTU 1500, rmem_max 160 KiB.
- FIT in mtd3 (05.02.2020): fpga@1 962 568 Byte md5 36b9ff2781bd1c7f13c01c5e923d9dc0 (≠ offizielles v0.31-Bit
  660ad21994c0e16e5eed06f0da3c9ebf, 960 760 Byte – gleiche HDL-Generation, anderer Vivado-Lauf), Kernel 3,68 MiB,
  Ramdisk 22,04 MiB; fit_size 0x1AB13A3.

## 2. Build (WSL1 dablab, 8 Jobs, nice 10)
- plutosdr-fw v0.31, Submodule buildroot b31cc4d (2019.02.2), linux 387d584; Toolchain Bootlin gcc 7.3.0.
- Kernel: 3 min, zImage 3 415 472 Byte; Buildroot: ≈ 20 min, rootfs.cpio.gz 5 612 214 Byte.
- Host-Probleme und Lösungen: Buildroot verlangt `python` (python-is-python3); host-fakeroot 1.20.2 kompiliert nicht
  mit glibc 2.39 (`_STAT_VER`, Patch br2ext/patches/fakeroot) und braucht in WSL1 `--with-ipc=tcp` (kein SysV-IPC);
  dtc im Kernel 4.14 braucht `extern YYLTYPE yylloc` (gcc ≥ 10); BR2_EXTERNAL erst nach `mkdir output`.
- Ergebnis `out/pluto.frm` 10 043 344 Byte (9,6 MiB) – SHA256 siehe `out/SHA256SUMS`.

## 3. RAM-Boot-Pfad (DFU) – nachgewiesen mit dem Backup-Image
- `device_reboot ram` → Windows zeigt „USB download gadget“ 0456:b674 (WinUSB automatisch), `dfu-util -l` zeigt
  `alt=1 name="firmware.dfu"`, `alt=0 dummy.dfu`.
- `dfu-util -a firmware.dfu -D backup/mtd3_fit_backup.dfu` (27 988 915 Byte) → „Done!“, `-e` → Gerät bootete die alte
  Firmware aus dem RAM: `/proc/cmdline … rw earlyprintk …` (ramboot_verbose), `uptime 0 min`, `device-fw v0.31-4-g9ceb-dirty`.
  Flash unverändert (fit_size weiterhin 1AB13A3).
- Fallstricke: `dfu-util -e` ohne `-d 0456:b673,0456:b674` findet am PC ein zweites DFU-Gerät (ESP32-S3 303A:1001);
  `-d 0456:b674` allein verweigert den Download wegen Suffix-PID b673.

## 4. Erster RAM-Boot der eigenen Firmware (Build 1, 02:42) – FEHLGESCHLAGEN
- Nach `-e` erschien kein USB-Gerät. Ursache (offline nachgewiesen): `taskset` fehlte in der Busybox-Konfiguration
  (board/pluto/busybox-1.25.0.config), S23udc startete iiod über `/usr/bin/taskset` → iiod nicht gestartet → keine
  FunctionFS-Deskriptoren → `echo ci_hdrc.0 > UDC` scheitert → gesamtes USB-Gadget (RNDIS, ACM, MSD, IIO) fehlt.
- Folge: Gerät hängt ohne USB im RAM-Image; **nur Aus-/Einstecken hilft** (xHCI-Neustart per pnputil: „Access is denied“).
- Korrektur (Build 2, 02:49): busybox `CONFIG_TASKSET=y`+FANCY; S23udc startet iiod wieder exakt wie im Original und setzt
  die Affinität danach (`taskset -p MASK PID`, nur wenn taskset existiert) – damit kann die Änderung das Gadget nicht
  mehr blockieren. Build 2 ist in `out/` (SHA256 pluto.frm 4c2be866…, pluto.dfu ca376b98…), noch nicht gebootet.

## 5. Build 2 im RAM-Boot (23.10.2026, 11:05–11:40) – BESTANDEN
Ablauf: Server 8792 gestoppt, `boot_ram.cmd out\pluto.dfu`. Das erste Detach im Skript lief ohne `-a firmware.dfu` und wurde
verworfen (Gerät blieb im U-Boot-DFU, nichts hing) → Detach von Hand, Skript korrigiert. Gerät nach ~10 s per Ping da.
COM33-Mitschnitt (`test/bootlog_build2.txt`): COM33 ist das ACM-Gadget von Linux, U-Boot/Kernel-Bootmeldungen laufen über die
interne UART (ttyPS0, nicht am USB) – mitgeschnitten ist nur der Login-Prompt vor/nach dem Neustart.

Vorabtest ohne Neustart (unter Werksfirmware, Binaries von Build 2 mit eigenem ld-linux/libc aus /tmp): iiod 0.25 `-V`/`-h` ok,
busybox `taskset` ok, libiio 0.25 lokal erkennt alle 5 Geräte, iqplay ok, dablab-agent liefert Status, TX bleibt −89,75 dB.

| Test | Ergebnis | Datei (test/results/) |
|---|---|---|
| Version | `/opt/VERSIONS` device-fw v0.31-dablab.20261023, Kernel 4.14.0-dablab-g387d584d4-dirty #2, `iio_info -u ip:` → Backend 0.25, `fw_version v0.31-dablab.20261023`, `hw_model … (Z7010-AD9364)` (Hack aktiv) | build2_basic_agent_rate.txt, build2_iio_writedev.txt |
| Systemtuning | 2 Kerne, max_block_size 67108864, sched_rt_runtime_us −1, TX-DMA-IRQ 48 → Maske 2 (Kern 1), iiod-Maske 2, CMA 256 MiB frei | build2_basic_agent_rate.txt |
| dablab-agent | `/api/status`, `/api/rf`, `/api/rate`, `/api/mod/*` antworten; Start-/Endzustand −89,75 dB | build2_basic_agent_rate.txt |
| Rate 2,048 MS/s | `ad9361_set_bb_rate(2048000)` → 2 047 999 S/s, FIR an (TF 8,192 MHz = 4×) | build2_fir_proof.txt |
| eigener FIR | 16 Taps INT 2/DEC 2 → `tx_path_rates … TF:4095999 TXSAMP:2047999` (= 2×); 32 Taps INT 4 → TF 8,192 MHz (= 4×); 15 Taps abgelehnt; ohne FIR lehnt der Treiber 2,048 MS/s ab (`Invalid argument`) | build2_fir_proof.txt |
| iqplay 9,142857 MS/s | Datei 73 MB (Zufalls-IQ), 60 s lokal, Kern 1 SCHED_FIFO 50: 551 999 931 Samples, **Underflows 0**, Exit 0 | build2_iqplay_9M.txt |
| lokale DMA-Grenze | iqplay -z je 30 s bei 14 / 20 / 30 / 40 / **61,44 MS/s**: jeweils **Underflows 0**, CPU Kern 1 5–15 % (libiio 0.25 lokal = mmap, keine Kopie) | build2_dma_limit.txt |
| UDP PC→Pluto (RNDIS) | 1316-Byte-Pakete, 10 s je Stufe, Empfang = Kernelzähler InDatagrams+NoPorts: 35 und **60 Mbit/s verlustfrei**; 100 Mbit/s 96 % Verlust (rx_drop am usb0) → Grenze 60…100 Mbit/s; Ziel ≥ 35 Mbit/s erfüllt, NCM nicht nötig (per `fw_setenv usb_net ncm` verfügbar, ungetestet). iperf3 3.6↔3.16 im UDP-Modus unbrauchbar | build2_udp_rndis.txt |
| iiod-Durchsatz | `iio_writedev -b 1048576 -T 0`, 400 MB Nullen, TX 30,72 MS/s: **usb: 27,9 MB/s (6,98 MS/s)**, ip: 26,3 MB/s (vorher v0.31/iiod 0.18: 25,9 / 26,0). Matrix: iiod Kern 0/1/beide ohne Einfluss; Blöcke 256 KiB 22 MB/s, 512 KiB 26, **1 MiB 28,3**, 2 MiB 16,6, 4 MiB 11,8. Ziel 30 MB/s **nicht erreicht** – Engpass USB-Gerätepfad (ChipIdea/FunctionFS), nicht CPU | build2_iio_writedev.txt |
| DAB-Streaming | PC-Server + `tx_6c_max.py 60` (60 dB Dämpfung), 64,7 s, 4,096 MS/s, **Underruns 0**, src_stalls 0, danach TX stop → 89,75 dB, muted | build2_dab_stream_60s.txt |
| U-Boot-Env | `fw_printenv` (76 Variablen) **identisch** mit Backup (attr_name/attr_val, ethaddr, fit_size 1AB13A3) | fw_printenv_build2_ramboot.txt |
| DVB-T (Modulator-Agent) | `/tmp/dvbt_mod_iio` 8k/64-QAM/7/8/GI 1/32 auf dieser Firmware: `rt_factor 0,930` (60,07 s Signal in 64,6 s) – schwerster Modus noch knapp unter Echtzeit; Optimierung beim Modulator-Agenten | (/tmp/dac.log auf dem Gerät) |

## 6. Build 3 (v0.31-dablab.20261023-b3) – RAM-Test und Flash (23.10.2026, 12:50–13:20)
Inhalt gegenüber Build 2: dablab-agent 0.5 (SIGTERM-Fix: sigaction ohne SA_RESTART; Underflow-Zähler nur während eines
Modulatorlaufs; `cma_free_kb`), Modulatoren aus `mod/` fest in /usr/bin (dvbt_mod, dvbc_mod, rrc_fir_gen, dvbmod_selftest;
HAVE_LIBIIO, NEON, -O3, gegen libiio 0.25; enthält output.c-Fix „Schreibzeiger nach jedem Push“ und „fremdes TX-FIR aus“).
Build-Fehler unterwegs: CFLAGS als make-Argument überschrieb `CFLAGS += -DHAVE_LIBIIO` → dvbt_mod ohne IIO („ohne HAVE_LIBIIO
gebaut“); behoben (CFLAGS als Umgebung) + Build-Prüfung im Paket. pluto.frm SHA256 76a35113…, pluto.dfu 004db9f7….

| Test | RAM-Boot Build 3 | nach Flash | Datei (test/results/) |
|---|---|---|---|
| Boot | `earlyprintk` (RAM) | `quiet loglevel=4` (Flash, qspiboot) | build3_postflash.txt |
| Version | fw_version v0.31-dablab.20261023-b3, Agent 0.5 | gleich; `iio_info -u usb:` vom PC: Backend 0.25, hw_model …Z7010-AD9364 | build3_basic_agent.txt, build3_postflash.txt |
| Agent | Status ok, SIGTERM-Stop/Start ok, Zähler im Leerlauf 0 | Status ok | |
| dvbmod_selftest | SELFTEST OK, ifft_8192_crc=fb3a696f (= Referenz) | SELFTEST OK | |
| DAB vom PC (60 dB) | 65 s, Underruns 0, src_stalls 0 | 65 s, Underruns 0, src_stalls 0 | build3_dab_stream_60s.txt, flash_dab_stream_60s.txt |
| DVB-T stumm, /usr/bin/dvbt_mod, 8k/16-QAM/2/3/GI 1/4, 9,142857 MS/s, 30 s | rt_factor 1,001, stuffed/dropped 0, Underflow 0 während des Laufs (1 Ereignis beim Pufferabbau am Ende) | identisch | build3_dvbt_mute_rx.txt, build3_dvbt_loopback.txt, flash_dvbt_loopback.txt |
| Inhaltsprüfung | AD9361 digitaler TX→RX-Loopback (debugfs `loopback=1`, kein HF): gr-dtv 10 240 Pakete **0 Bitfehler**, TPS BCH ok (8k/16-QAM/2/3/1/4), Dienst „DABLAB Test“ | 10 864 Pakete **0 Bitfehler** | ts_align_compare.py |
| Eigenempfang über die Luft bei −89,75 dB | nicht decodierbar (Pegel zu klein, gr-dtv bricht ab) – daher Loopback | – | build3_dvbt_mute_rx.txt |
| Flash | – | pluto.frm aufs Laufwerk I: „PlutoSDR“ (SHA256 der Kopie geprüft), Auswerfen 13:06:40, `dd` → mtdblock3 ≈ 140 s, Reset, nach 12 s erreichbar; mtd3[0..fit_size] md5 d3570fbe… = pluto.itb | build3_postflash.txt |
| U-Boot-Env | – | 76 Variablen, einzige Änderung `fit_size` 1AB13A3 → 9A8D53 (setzt update.sh; = Größe pluto.itb); attr_name/attr_val/ethaddr unverändert | fw_printenv_before_flash.txt, fw_printenv_after_flash.txt |

## 7. Offen / Hinweise
- Agent zählt den einen Underflow beim Pufferabbau am Laufende mit (Modulator noch nicht eingesammelt) – kosmetisch.
- iiod ≥ 30 MB/s nicht erreicht (27,9 MB/s); Engpass USB-Gerätepfad.
- Mitschnitt über die Luft braucht Pegel; die Loopback-Prüfung ist der stumme Ersatz (prüft den digitalen Inhalt bis vor den DAC).

## 8. Build 4 (v0.31-dablab.20261023-b4) – gebaut 23.10. 13:32, RAM-Test steht aus (Gerät beim Modulator-Agenten)
- pluto.frm SHA256 819de8e8…, pluto.dfu 7d79582f… (out/SHA256SUMS); geflashter Build 3 archiviert in out/build3/.
- Inhalt: mod/ aktueller Stand (Fenster 32/8, --slow, alle Fixes; Quelle identisch mit G:), dablab-agent 0.6
  (`cmd:"pal"`/`"cyclic"` → iqplay -N -c; `/api/upload`, `/api/files`, `/api/files/delete`; `mod.stats` = letzte
  Modulator-JSON-Zeile, `mod.clipped`, `mod.rt_factor`), iqplay -N (FIR aus, Rate direkt) und -c mit ganzer Datei als Puffer.
- Vorab ohne Gerät geprüft (results/build4_agent06_hosttest.txt): ARM-Kompilat ok; x86-Funktionstest mit Stub-libiio:
  Upload 5 MiB bytegenau, ungültige Namen abgelehnt, Liste/Löschen ok, pal-Kommandozeile korrekt, Stats-Durchreichen ok,
  SIGTERM ok. PAL-Testschleife pal_test.iq (5,12 MB) mit mod/tools/pal_bg_gen.py erzeugt und offline geprüft.
- iiod-Durchsatz: unverändert (USB-Pfad unangetastet). Befund: iiod 0.25 nutzt für FunctionFS bereits AIO, aber nur
  1 offenen Auftrag. Nächste Hebel (Messung nötig, Nutzer anwesend): ChipIdea BURSTSIZE/ITC zur Laufzeit
  (`test/usb_burst_tune.sh`), dann als DT-Eigenschaft (`rx-burst-size-dword`, `itc-setting`); alternativ iiod-Patch mit
  Warteschlangentiefe 2.
- Nach „Gerät frei“: `boot_ram.cmd out\pluto.dfu`, `test/build4_ramtest.sh` (basic/agent, Upload, PAL über cmd:pal mit
  Loopback + pal_check, DVB-T-Loopback bitexakt, Env-Abgleich), DAB 60 s vom PC; Flash nur mit Freigabe.
