# dvbmod_lab – DVB-T- und DVB-C-Modulatoren für den ADALM-Pluto (Labor-Testsender)

Portable C99-Programme (x86 zum Testen, Cortex-A9/NEON auf dem Pluto) mit gemeinsamer Bibliothek
`libdvbmod_lab.a`. Keine exotischen Abhängigkeiten: nur libc, libm, pthread; optional libiio
(`HAVE_LIBIIO=1`). Keine Gleitkommaoperationen im Signalpfad (Coder, Interleaver, Mapper, IFFT, RRC
sind Festkomma; `double` nur bei der Initialisierung von Tabellen).

| Programm | Norm | Zweck |
|---|---|---|
| `dvbt_mod` | ETSI EN 300 744 V1.6.1 (nicht-hierarchisch) | 2k/8k, QPSK/16/64-QAM, CR 1/2…7/8, GI 1/4…1/32, BW 5/6/7/8 MHz |
| `dvbc_mod` | ETSI EN 300 429 V1.2.1 (Annex A, ITU-T J.83 A) | 16/32/64/128/256-QAM, Symbolrate frei, 2 oder 1 Sample/Symbol |
| `rrc_fir_gen` | AD9361 `filter_fir_config` | RRC-α-0,15-Koeffizienten, 2×-Interpolation im TX-FIR (für `dvbc_mod --sps 1`) |
| `dvbmod_selftest` | – | Bausteintest + Mikrobenchmark (`dvbmod_selftest micro`) |

⚠️ Senden nur abgeschirmt. Die IIO-Ausgabe setzt beim Öffnen immer zuerst `hardwaregain = -89.75` und
hebt die Dämpfung nur mit `--enable --atten X` an (0…89,75 dB).

## Bauen

```sh
make                                   # x86, -O3
make HAVE_LIBIIO=1                     # mit lokalem IIO-Kontext (libiio ≥ 0.19, pkg-config)
make CC=arm-linux-gnueabihf-gcc STATIC=1 \
     CFLAGS="-O3 -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard"      # statisch für den Pluto
```
`CC`, `CFLAGS`, `LDFLAGS` sind überschreibbar (Buildroot: `make CC="$(TARGET_CC)" CFLAGS="$(TARGET_CFLAGS)" HAVE_LIBIIO=1`).
Mit `-mfpu=neon` werden automatisch die NEON-Pfade (IFFT, Bit-Interleaver) übersetzt; sie sind bitidentisch
zur C-Referenz (gleiche Rundungsregeln, Prüfsumme im Selbsttest: `ifft_8192_crc=fb3a696f` auf x86 und ARM).

Dateien: `lib/` (Bibliothek), `dvbt_mod.c`, `dvbc_mod.c`, `rrc_fir_gen.c`, `tools/dvbmod_selftest.c`,
`tests/` (Prüfskripte + `results/*.json`), `Makefile`, `LICENSE`.

## dvbt_mod

```
dvbt_mod [-i SPEC] [-o PFAD] --mode 2k|8k --const qpsk|16qam|64qam --rate 1/2|2/3|3/4|5/6|7/8
         --guard 1/32|1/16|1/8|1/4 --bw 5|6|7|8 [--backoff dB] [--cell-id N] [--tps-lp-same]
         [--frames N | --seconds S] [--loop] [--udp-buffer N] [--threads 1|2] [--affinity] [--rt]
         [--realtime] [--profile] [--bench N] [--dump PREFIX] [--quiet]
         IIO: -o iio: [--lo HZ] [--atten DB] [--rfbw HZ] [--iio-buf SAMPLES] [--iio-kbufs N] [--cyclic] [--enable] [--fir DATEI]
```
* `-i`: Datei, `-` (stdin), `udp://[addr]:port` (RTP wird erkannt und entfernt, Multicast-Join bei
  224/8…239/8), `prbs[:N]` (synthetischer TS, N Pakete), `null`. Standard `prbs` (für Benchmarks).
* `-o`: Datei, `-` (stdout, int16 I/Q interleaved, little endian), `/dev/null`, `iio:` (lokaler Kontext)
  oder `iio:ip:192.168.2.1` (nur zum Testen vom PC).
* Abtastrate = BW·8/7 MS/s (8 MHz → 9,142857 MS/s), Ausgabe int16; Pegel: RMS = −`backoff` dBFS (Standard 12 dB).
* TPS: Synchronwörter, Längenkennung 010111 (bzw. 011111 + Zellkennung bei `--cell-id`), Konstellation,
  Hierarchie 000, Coderate HP, Coderate LP = 000 (Norm, nicht-hierarchisch; `--tps-lp-same` setzt sie wie
  gr-dtv auf HP), Guard, Modus, s48–s53 = 0, BCH(67,53). Piloten (verstreut/kontinuierlich, ±4/3) und
  TPS-DBPSK aus PRBS x^11+x^2+1. Gegen gr-dtv geprüft: Pilot-, TPS- und Datenträger identisch.
* Bei Dateiende wird der laufende Superrahmen mit Nullpaketen aufgefüllt; ganze Superrahmen (272 Symbole).
* Der Faltungsinterleaver wird mit verwürfelten Nullpaketen vorbelegt (sonst entstehen aus der
  Null-Historie lange Nullfolgen → Spitzen > 0 dBFS im ersten Symbol). Receiver-neutral.
* Statuszeilen (stderr, JSON, 1/s): Symbole, Signalzeit, Echtzeitfaktor, CPU je Thread, TS-Pakete,
  gestopfte/verworfene Nullpakete, Pufferfüllstand. Am Ende eine `done`-Zeile mit µs/Symbol je Thread
  (`--profile`: je Stufe).
* `--dump PREFIX` schreibt die Zwischenstufen (`.scr .rs .ci .punct .yp .y`) für Vergleiche.

Pipeline (2 Threads, Ringpuffer mit 8 Frequenzbereichsvektoren, keine Kopien im Signalpfad):
Kern 0: TS → Verwürfelung (vorberechnete 1504-Byte-XOR-Folge) → RS(204,188) (4×32-Bit-Tabellen) →
Faltungsinterleaver (12-Paket-Historie, Byte i um i mod 12 Pakete verzögert) → Faltungscoder K=7
(Nibble-Tabellen) + Punktierung (Maskentabellen, Bitpacker) → Demux/Bit-Interleaver (NEON-Masken je
126er-Block) → Symbol-Interleaver (H(q)-Tabelle) → Mapper (32-Bit-Tabelle re|im) → Piloten/TPS → Vektor.
Kern 1: IFFT int16 (Stockham Radix-4 NEON, Twiddles Q15, Skalierung 1/2 je Stufe durch
Halbierungsaddierer, letzte Radix-2-Stufe schreibt direkt interleaved I/Q + Guard) → Ausgabe.

### Nutzbitraten (Mbit/s, 8 MHz; 7/6/5 MHz: ×7/8, ×6/8, ×5/8)

Pakete je Superrahmen: 8k = 1008·v·CR, 2k = 252·v·CR (v = 2/4/6). Bitrate = Pakete·1504 Bit / (272·(N+G)·T).

| Konst. | CR | GI 1/4 | GI 1/8 | GI 1/16 | GI 1/32 |
|---|---|---|---|---|---|
| QPSK | 1/2 | 4,976 | 5,529 | 5,855 | 6,032 |
| QPSK | 2/3 | 6,635 | 7,373 | 7,806 | 8,043 |
| QPSK | 3/4 | 7,465 | 8,294 | 8,782 | 9,048 |
| QPSK | 5/6 | 8,294 | 9,216 | 9,758 | 10,053 |
| QPSK | 7/8 | 8,709 | 9,676 | 10,246 | 10,556 |
| 16-QAM | 1/2 | 9,953 | 11,059 | 11,709 | 12,064 |
| 16-QAM | 2/3 | 13,271 | 14,745 | 15,612 | 16,086 |
| 16-QAM | 3/4 | 14,929 | 16,588 | 17,564 | 18,096 |
| 16-QAM | 5/6 | 16,588 | 18,432 | 19,516 | 20,107 |
| 16-QAM | 7/8 | 17,418 | 19,353 | 20,491 | 21,112 |
| 64-QAM | 1/2 | 14,929 | 16,588 | 17,564 | 18,096 |
| 64-QAM | 2/3 | 19,906 | 22,118 | 23,419 | 24,128 |
| 64-QAM | 3/4 | 22,394 | 24,883 | 26,346 | 27,144 |
| 64-QAM | 5/6 | 24,882 | 27,647 | 29,273 | 30,160 |
| 64-QAM | 7/8 | 26,126 | 29,029 | 30,737 | 31,668 |

(`dvbt_mod` gibt die exakte Rate in der ersten Statuszeile als `bitrate_bps` aus, z. B. 8k/64-QAM/2/3/1/32: 24 128 342,25 b/s.)

## dvbc_mod

```
dvbc_mod [-i SPEC] [-o PFAD] --qam 16|32|64|128|256 --sr BAUD [--sps 2|1] [--taps 128] [--backoff dB]
         [--packets N | --seconds S] [--loop] [--realtime] [--udp-buffer N] [--quiet]  IIO wie oben, zusätzlich --fir DATEI
```
* `--sps 2`: RRC α = 0,15 im Programm (Polyphase, Q15, `--taps` Länge, Standard 128 = 64 Symbole),
  Ausgaberate 2·SR. `--sps 1`: ein Sample je Symbol, Ausgaberate = SR; die Formung übernimmt der
  AD9361-TX-FIR mit 2×-Interpolation (`rrc_fir_gen -o rrc.fir`, dann `--fir rrc.fir`; bei IIO wird die
  Datei in `filter_fir_config` geschrieben und `filter_fir_en` gesetzt, `sampling_frequency` = SR).
* Nutzbitrate = SR · log2(M) · 188/204: 6,9 MBd: 16-QAM 25,435 · 32-QAM 31,794 · 64-QAM 38,153 ·
  128-QAM 44,512 · 256-QAM 50,871 Mbit/s; 3,45 MBd: die Hälfte (256-QAM 25,435 Mbit/s); 6,875 MBd/256-QAM 50,686 Mbit/s.
* Konstellation nach Figur 7/8 der Norm (Quadrant 1 abgelesen, Quadranten 2/3/4 durch Drehung +π/2, +π,
  +3π/2 mit MSB 10/11/01, Tabelle 1), Differenzcodierung der 2 MSB nach Kap. 8, Byte→m-Tupel MSB zuerst.
* Pegel: RMS = −`backoff` dBFS (Standard 10 dB, 256-QAM-PAPR ≈ 8 dB nach RRC).

## rrc_fir_gen

```
rrc_fir_gen [--taps 128] [--alpha 0.15] [--sr 6900000] [--gain 0|-6] [--kaiser BETA] [--no-rx] [-o datei]
```
Erzeugt `TX 3 GAIN 0 INT 2` / `RX 3 GAIN -6 DEC 2` / `BWTX` / `BWRX` und 128 Koeffizientenzeilen `tx,rx`
(Q15; größter Tap = 32767 wie bei ADIs `fir_128_2`; DC-Verstärkung je Polyphase ≈ 0,96 = −0,35 dB,
weil der RRC-Mitteltap sonst 1,04 > 1 wäre). Die stderr-JSON-Zeile nennt Passband-Welligkeit und
Restspektrum. Prüfung: `tests/run_tests.sh` bildet den FIR-Weg in numpy nach (2×-Interpolation mit den
erzeugten Taps) und decodiert bitexakt (Ergebnisse `dvbc_*_sps1`).

Hinweise AD9361: `sampling_frequency` ist die Rate am FIR-Eingang (hier SR); 128 Taps erfordern
DAC-Takt/TX-Rate ≥ 8 (bei 6,9 MS/s gegeben); die Datei ist für beide Richtungen gültig (RX DEC 2 mit −6 dB).

## TS-Eingang und Stopfen

* Datei/stdin: blockierendes Lesen; Synchronisation auf 0x47 (188- oder 204-Byte-Raster, 204 wird auf 188 gekürzt).
* UDP: Lesethread, Ring in Paketen (`--udp-buffer`, Standard 2000 ≈ 376 kB). Ist der Ring leer, wird ein
  Nullpaket gestopft; über 75 % Füllstand werden eingehende Nullpakete (PID 0x1FFF) verworfen, nie Nutzlast.
  So bleibt die PCR unangetastet (Taktquelle ist der Pluto-DAC); bei ±20 ppm Ratenabweichung wandert der
  Füllstand um ≈ 0,5 Pakete/s bei 24 Mbit/s und wird durch die Nullpaket-Regelung ausgeglichen.
  Voraussetzung: der Quell-TS enthält mindestens ≈ 0,1 % Nullpakete (ffmpeg `-muxrate` etwas über der
  Nutzrate oder TSDuck `regulate`). Füllstand, gestopfte und verworfene Pakete stehen in den Statuszeilen.
* `--realtime` bremst Datei/stdout-Ausgabe auf die nominale Abtastrate (PC-Streaming-Weg).

## IIO-Ausgabe (HAVE_LIBIIO)

`iio_create_local_context()` (oder URI), `ad9361-phy`: `hardwaregain=-89.75` zuerst, optional FIR laden,
`sampling_frequency`, `rf_bandwidth`, LO; `cf-ad9361-dds-core-lpc`: voltage0/1 aktiviert,
`iio_device_set_kernel_buffers_count(--iio-kbufs)`, `iio_device_create_buffer(--iio-buf, cyclic)`. Fortlaufend:
Samples werden direkt in `iio_buffer_start()` geschrieben und mit `iio_buffer_push()` übergeben (keine
Zwischenkopie, DMA aus dem mmap-Puffer). `--cyclic`: eine Füllung (z. B. `--iio-buf 2297856` = ein
8k-Superrahmen bei GI 1/32), dann läuft der DMA ohne CPU-Last weiter. Unter x86 wurde die Variante nur
übersetzt und gelinkt (libiio 0.25); ein DAC-Test auf dem Pluto stand bei Abgabe noch aus (Freigabe).

## Verifikation (tests/)

| Skript | Zweck |
|---|---|
| `run_tests.sh` | Gesamtlauf (x86/WSL): 9 DVB-T-Modi, 10 DVB-C-Fälle, JSON nach `results/` |
| `dvbt_rx_grdtv.py` | gr-dtv-Empfänger (GNU Radio 3.10.12, apt) ohne GUI: IQ → TS |
| `dvbt_tx_grdtv.py` | gr-dtv-Referenzsender (zum Sample-/Trägervergleich; in 3.10 enthält `dvbt_reference_signals` bereits die IFFT) |
| `iq_compare.py` | Sample-genauer Vergleich zweier IQ-Dateien (Skalierung, SNR je Symbol) |
| `iq_analyze.py` | RMS, PAPR, Clipping, 4-kHz-PSD, Schulter, DVB-T-Maskenpunkte (3,9/4,2/6 MHz) |
| `dvbc_demod.py` | eigener numpy-DVB-C-Demodulator: RRC-Matched-Filter, Abtastphase, Entscheidung, Differenzdecodierung, Deinterleaver (Sync-Suche), RS-Decoder (reedsolo), Entwürfelung, Bitvergleich |

Ergebnisse (x86, WSL1, Test-TS = ffmpeg MPEG-2/MP2, 2 s, 4 Mbit/s, 5272 Pakete): siehe
`tests/results/summary.json` und die Zusammenfassung am Ende dieser Datei.

Zusätzliche Nachweise: `--dump` + gr-dtv-Sonden: Verwürfelung, RS-Parität, Interleaver und punktierte
Codebits sind Byte für Byte identisch mit gr-dtv (nach dem Vorbefüllen der Interleaver-Historie); im
Frequenzbereich stimmen Piloten, TPS und Datenzellen überein (Differenz = int16-Quantisierung).

## Leistung

### x86 (WSL1, Ryzen/Intel-Desktop, 1 Thread, skalare C-IFFT)
8k/64-QAM/7/8/GI 1/32: Coder ≈ 70 µs/Symbol, IFFT ≈ 200 µs/Symbol → Echtzeitfaktor ≈ 4 (Symbol 924 µs).

### Pluto (Zynq 7010, 2× Cortex-A9 @ 667 MHz, NEON, statisch gelinkt, Firmware v0.31, /tmp)
Mikrobenchmark (`dvbmod_selftest micro`), Gesamtlauf (`dvbt_mod --bench`), Dauerläufe (`tests/pluto/*.sh`).

Messreihe 8k/64-QAM/7/8/GI 1/32/8 MHz (Symbol 924,0 µs), Ausgabe nach /dev/null, Rohdaten `tests/results/pluto_bench.json`.
**Achtung:** während run2 lief auf dem Pluto ein fremder Prozess (`pluto_dvb`, Firmware-Agent) mit ≈ 50 % eines Kerns.

| Variante | Coder µs/Symbol (FEC / Bit-IL / Sym-IL / Rahmen) | IFFT µs/Symbol | RT-Faktor | Bemerkung |
|---|---|---|---|---|
| run1 Baseline, 2 Threads + Affinität | 1056 (722 / 186 / 59 / 150) | 590–609 | 0,705 | 64-Bit-Akkus auf ARM32, skalarer Bit-IL |
| run2 optimiert, 1 Thread | 690 (422 / 78 / 59 / 108) | 601 | 0,691 | |
| run2, 2 Threads ohne Affinität | 690 | 601 | 0,697 | Fremdlast verdrängt |
| run2, 2 Threads + Affinität | 667 | 573 | 0,821 | |
| **run2, 2 Threads + Affinität + SCHED_FIFO** | **677 (418 / 76 / 59 / 112)** | **614** | **1,344** | `--affinity --rt` |
| run2, IFFT-Stufenfusion (Varianten) | – | 589–660 (statt 537 im Mikrotest) | 0,75–0,77 | langsamer auf dem A9 → `FFT_FUSED=0` (Standard) |

60-s-Dauerläufe (`--affinity --rt`, ungetaktet → /dev/null):

| Modus | Signal (s) | Wandzeit (s) | RT-Faktor | Coder µs/Sym | IFFT µs/Sym |
|---|---|---|---|---|---|
| 8k 64-QAM 7/8 GI 1/32 | 60,07 | 44,51 | **1,349** | 682 | 619 |
| 8k 64-QAM 7/8 GI 1/4 | 60,01 | 37,23 | 1,612 | 685 | 636 |
| 2k 64-QAM 7/8 GI 1/32 | 60,01 | 44,41 | 1,351 | 171 | 126 |

Getakteter 60-s-Lauf (`--realtime`, nominal 9,142857 MS/s): 60,067 s Signal in 60,068 s, keine Aussetzer;
**Eigenlast Kern 0 = 73 %, Kern 1 = 70 %** (CPU-Zeit der Threads). /proc/stat zeigte Kern 0 80 % / Kern 1
100 % *einschließlich* des Fremdprozesses – ohne ihn ist das Ziel „beide Kerne < 90 %“ erfüllt, mit ihm nicht.

ARM-Einschätzung (gemessen): alle Modi bis einschließlich 8k/2k 64-QAM 7/8 bei 8 MHz laufen in Echtzeit mit
≈ 35 % Reserve, **sofern** `--affinity --rt` (SCHED_FIFO) gesetzt ist und kein zweiter rechenintensiver Prozess
läuft. Niedrigere Coderaten/Konstellationen und 5/6/7-MHz-Kanäle haben mehr Reserve (Coder-Last ∝ Bitrate,
Abtastrate ∝ Bandbreite). Die IIO-DMA-Ausgabe (memcpy in den mmap-Puffer, ≈ 37 MB/s) ist noch nicht gemessen
(kein DAC-Zugriff freigegeben); sie läuft im IFFT-Thread und kostet schätzungsweise 5–10 % eines Kerns.

## Spektrum: Symbolfensterung und AD9361-Kanalfilter

**Fensterung** (`--window W`, Standard 8k: 32, 2k: 8 Samples): Jedes OFDM-Symbol wird um W Samples zyklisch
verlängert und mit dem Anfang des nächsten Symbols überblendet (Raised Cosine, Q15, NEON). Die Überblendung
liegt im Guard, die wirksame Guard-Länge sinkt also um W (8k/GI 1/32: 28 µs → 24,5 µs). Rechenaufwand: unter
1 µs pro Symbol. Messung (x86, digital, 4-kHz-PSD, Schulter = Pegel 1,05…1,25 × 3,8 MHz relativ zum Nutzband):

| Modus | W=0 | W=8 | W=16 | W=24 | W=32 | W=48 | W=64 |
|---|---|---|---|---|---|---|---|
| 8k 64-QAM 2/3 1/32 (Schulter dBc) | −35,7 | −44,3 | −53,6 | −60,3 | **−64,4** | −68,0 | −69,4 |
| 8k: bei ±4,2 MHz (dBc) | −36 | −41,2 | −48,1 | −57,7 | **−65,8** | −67,6 | −69,5 |
| 8k: gr-dtv bitexakt (6 getestete Modi) | ja | ja | ja | ja | **ja** | ja | nein bei 16-QAM 3/4 GI 1/8 und QPSK 1/2 GI 1/16 |
| 2k 64-QAM 5/6 1/32 (Schulter dBc) | −31,1 | **−38,3** | −47,2 | −54,7 | −59,7 | −65,0 | −67,2 |
| 2k: gr-dtv bitexakt | ja | **ja** | nein | nein | nein | nein | nein |

Warum gr-dtv bei großem W aussteigt: Dessen Zeitsynchronisation (van de Beek, λ = |γ| − ρ/2·Φ mit festem
ρ) erwartet einen exakt zyklischen Guard. Mit Fenster wird das λ-Maximum negativ und wandert um ≈ W/2. Das ist
eine Eigenheit des Prüfempfängers; Handelsempfänger legen das FFT-Fenster ohnehin in den Guard zurück.
Ziel „≤ −45 dBc bei ±4,2 MHz“: 8k mit Standardfenster erfüllt (−65 dBc). 2k erfüllt es nur zusammen mit dem
Kanalfilter.

**AD9361-TX-FIR als Kanalfilter** (kostet keine CPU): `rrc_fir_gen --lowpass --fs 9142857 --pass 3805000 --stop 4200000 --atten 70 -o lp.fir`
erzeugt 128 Taps, `TX 3 GAIN 0 INT 1`, Kaiser β = 6,76, Summe 32766 (DC −0,0 dB), Welligkeit ±0,003 dB, Sperrdämpfung
≥ 68 dB ab 4,2 MHz (Beispiel: `tests/results/dvbt8_txfir_lowpass_int1.fir`). Laden: `dvbt_mod … --fir lp.fir`.
Auf dem Pluto sind bei 9,142856 MS/s TX-FIR-Takt 36,6 MHz und DAC 146 bzw. 293 MHz (`tx_path_rates`), damit sind
128 Taps zulässig (DAC/TX × 16 ≥ 128). Numerische Nachbildung (Faltung der int16-Taps mit dem Modulatorsignal):

| Modus | Fenster | mit TX-FIR: Schulter | ±4,2 MHz | gr-dtv |
|---|---|---|---|---|
| 2k 64-QAM 5/6 1/32 | 0 | −77,3 dBc | −85,9 dBc | bitexakt |
| 2k 16-QAM 2/3 1/4 | 0 | −77,6 dBc | −86,2 dBc | bitexakt |
| 8k 64-QAM 2/3 1/32 | 0 / 64 | −79,0 / −85,2 dBc | −85 dBc | bitexakt |
| 8k 64-QAM 7/8 1/4 | 64 | −85,3 dBc | −86,4 dBc | bitexakt |

Am Gerät noch nicht nachgemessen (kein Analysator, Pegel nur auf Anweisung); im Analogteil begrenzen DAC-Rauschen
und Intermodulation des AD9361 die erreichbare Schulter (typisch −50…−60 dBc).

## DAC-Nachweis auf dem Pluto (stumm, −89,75 dB)

`tests/pluto/pluto_dac60.sh`: `dvbt_mod` (statisch mit libiio 0.25, nur lokales Backend) schreibt über
`iio_buffer_push` in `cf-ad9361-dds-core-lpc`. Unterlauf-Erkennung: FPGA-Register `up_status_unf` (HDL
`up_dac_common`, Adresse 0x22 → Byte 0x88 relativ zur DDS-Basis 0x79024000, Schreiben einer 1 löscht) per debugfs
(`echo 0x80000088 > …/direct_reg_access`). Ergebnis (dablab-Build 2019.02.2-gb31cc4d7, 8k/64-QAM/7/8/GI 1/32, W=32,
`--affinity --rt`, exklusives Zeitfenster):

| Lauf | Signal / Wandzeit | Echtzeitfaktor | Unterlauf-Flag (alle 10 s) | Last cpu0 / cpu1 |
|---|---|---|---|---|
| 60 s, DAC 9,142856 MS/s | 60,067 s / 60,056 s | 1,000 (getaktet) | **0 0 0 0 0** | 67,2 % / 67,0 % |
| Negativprobe `--slow 400` | 15,08 s / 17,83 s | 0,846 | 1 (wie erwartet) | 47 % / 48 % |

Die Raten blieben konstant, kein Fremdzugriff. Ein früherer Lauf brach ab, weil der PC-Server beim Verbinden
4,096 MS/s gesetzt hatte. Daraus folgt: `dvbt_mod` braucht den TX-Pfad exklusiv. Auf der Werksfirmware v0.31 ist
der DAC-Lauf nicht getestet (nur CPU-Läufe).

## Testsender K56/K57 (ausgeführt auf Anweisung)

Gestartet auf K56 (754 MHz, −40 dB, dann 0 dB), auf Wunsch umgestellt auf K57 (762 MHz, NIT V2, `make_k57.sh`), dann mit `--backoff 9` (gemessen 0,007 % gesättigte Samples, gr-dtv bitexakt, Schulter −54,5 dBc) auf K36 (594 MHz, NIT V3, `make_k36.sh`) und zuletzt K36 mit 7 MHz (8 MS/s, rf_bandwidth 7 MHz, TS 11,61 Mbit/s, NIT V4, `make_k36_7mhz.sh`, gr-dtv bitexakt), 0 dB; im Betrieb Echtzeitfaktor 1,00, Unterlauf-Flag 0 bei allen Abfragen (alle 2 min).

TS (`tests/k56/make_k56_ts.sh`): SMPTE-HD-Balken 720×576 MPEG-2 7 Mbit/s mit Uhr, 1 kHz MP2 192 k,
`service_name` „DABLAB Test“, NIT mit `terrestrial_delivery_system_descriptor` 754 MHz/8k/16-QAM/2/3/1/4,
CBR 13 270 588 b/s, 40 s, geloopt. Vorab über gr-dtv bitexakt (85 542 Pakete). Steuerung auf dem Pluto:
`sh /tmp/k56_ctl.sh start [TS] [LO] [dvbt_mod-Optionen]|gain DB|status|stop` (Start immer stumm, Pegel nur im Bereich 0…−89,75 dB, `stop`
stummt zuerst und stellt dann Rate, Bandbreite und FIR wieder her).

## Analog-TV PAL B/G (Zusatz, auf Anweisung)

`tools/pal_bg_gen.py` erzeugt eine nahtlose 160-ms-Schleife (4 Vollbilder = PAL-8-Halbbild-Sequenz, 1 280 000 Samples
bei 8 MS/s, alle Träger mit ganzzahligen Perioden): ITU-R-BT.470-Synchronisation (Halbbild- und Ausgleichsimpulse),
PAL-Farbe (Burst ±135°, V-Schalter), negative AM (Sync 100 %, Austast 73 %, Weiß 10 %), Restseitenband −0,75/+5 MHz,
FM-Ton +5,5 MHz (±50 kHz, 1 kHz, 50 µs, −13 dB). Bildträger im Basisband bei −2,25 MHz → TX_LO = Bildträger + 2,25 MHz.
Ausgabe zyklisch: `tests/k56/pal_ctl.sh start|gain|status|stop` (iio_writedev -c, FIR aus). Prüfung: `tools/pal_check.py`
(Hüllkurve → Bild); Datei und Pluto-RX-Mitschnitt demodulieren zum korrekten Bild (`tests/results/pal_file.png`,
`pal_rx.png`). Gesendet auf K36 (Bildträger 591,25 MHz, LO 593,5 MHz), 0 dB.

**Korrektur aus diesem Test:** Im AD9361 war ein fremdes TX-FIR (128 Taps, INT 4) aktiv, das `dvbt_mod`/`dvbc_mod`
nicht abschalteten. Seit dieser Version setzen beide `filter_fir_en=0`, wenn kein `--fir` angegeben ist.

**DVB-T K36 nach der FIR-Korrektur** (`tests/k56/dvbt_k36_restart.sh`): TX-FIR aus (TF = TXSAMP = 9,142856 MS/s), rf_bandwidth 8 MHz,
0 dB, Unterlauf 0. Pluto-RX-Mitschnitt (RX-FIR aus, RX-Bandbreite 9 MHz, `rx_flat.sh`): ±3,0 MHz flach, Ränder −4,7/−6,0 dB bei ±3,75 MHz
(TX+RX zusammen, beide Halbbandketten ohne FIR-Kompensation). Hinweis: RX-Filter-Umstellungen programmieren die BBPLL neu und verursachen
einen einmaligen TX-Unterlauf.

**Fehler in der IIO-Ausgabe (behoben, 23.10. 12:20):** `lib/output.c` merkte sich den Schreibzeiger `iio_buffer_start()`
nur einmal. Im mmap-Modus von libiio (mehrere Kernel-Puffer) zeigt der Puffer nach jedem `iio_buffer_push()` auf den
nächsten Block. Folge: `dvbt_mod`/`dvbc_mod` schrieben immer in denselben Block, die übrigen wurden mit veraltetem Inhalt
gesendet. Takt und Unterlauf waren in Ordnung (der stumme DAC-Nachweis prüfte nur diese), der Inhalt aber unbrauchbar,
und der Fernseher rastete nicht ein. Datei- und stdout-Ausgabe waren nie betroffen. Jetzt wird der Zeiger nach jedem
Push neu geholt. Nachweis: Pluto-Eigenempfang (TX → RX) auf 594 MHz (`tests/dvbt_analyze.py`: Pilotfolge 100 %,
TPS ok; gr-dtv: 11 584 Pakete, 0 Bitfehler, Dienst „DABLAB Test“). Lehre: Den DAC-Inhalt immer per Eigenempfang
prüfen, nicht nur Takt und Unterlauf.

**Live-Video über UDP (23.10.):** `tests/k56/stream_video.sh` (SD: ffmpeg MPEG-2 + MP2 live) und `tests/k56/stream_hd.sh`
(HD: vorab transkodiert, H.264 1080p25 High@4.0 9 Mbit/s CBR + AAC-LC 192k, SDT-Typ 0x19, tsp-Endlosschleife mit
continuity/pcradjust/regulate, NIT und EIT injiziert) → UDP 192.168.2.1:1234 → `dvbt_mod -i udp://0.0.0.0:1234 --udp-buffer 20000`.
Der UDP-Eingang füllt den Ring vor dem ersten Paket zur Hälfte vor (Jitter-Reserve) und nach einem Leerlauf erneut. Gemessen: 0
verworfene Pakete, keine Stopfpakete nach der Vorfüllung, Füllstand stabil um 45–50 %, Unterlauf 0. Eigenempfang mit gr-dtv
fehlerfrei (0 Diskontinuitäten). Am Fernseher als Programm „DABLAB Test“ empfangen.

## Erkenntnisse aus dem Empfangstest (abgeschirmter Laboraufbau, Fernseher als Empfänger)

Wichtigste Korrekturen aus dem Empfangstest: (1) `output.c` holt den libiio-Schreibzeiger nach jedem `iio_buffer_push()` neu;
(2) `dvbt_mod`/`dvbc_mod` schalten ein fremdes AD9361-TX-FIR ab, wenn kein `--fir` angegeben ist; (3) Inhaltsprüfung am DAC
immer per Eigenempfang (`tests/dvbt_analyze.py`, gr-dtv), nicht nur Takt und Unterlauf.

## dvbs_mod – DVB-S (EN 300 421)

`dvbs_mod -i TS -o iio: --sr 22000000 --rate 1/2|2/3|3/4|5/6|7/8 --sps 1 --fir rrc22.fir --lo HZ` – Energieverwürfelung, RS(204,188),
Faltungsinterleaver I=12, Faltungscode K=7 (171/133) mit Punktierung (Bibliothek), serieller Bitstrom paarweise auf (I,Q),
QPSK Gray, RRC α=0,35: `--sps 1` mit AD9361-TX-FIR (`rrc_fir_gen --alpha 0.35 --sr 22000000 --taps 64`, INT 2) oder `--sps 2` in Software.
Nutzbitrate = SR·2·CR·188/204 (22 MBd, 1/2: 20 274 510 b/s). Prüfempfänger `dvbs_rx` (C: Phasen-/Punktier-/Bit-Synchronisation,
Viterbi, Deinterleaver, RS-Syndrom, Entwürfelung) + `tests/dvbs_soft.py` (IQ → weiche Bits, auch Funk-Mitschnitt).
Software-Schleife bitexakt (1/2, 3/4, 7/8). Pluto (A9, ein Kern, Kodierkette ohne DMA): max. 59,8 / 43,9 / 37,8 MBd bei 1/2 / 3/4 / 7/8;
im Betrieb 22 MBd 1/2: 42 % eines Kerns, Echtzeitfaktor 1,00, Unterlauf 0. Eigenempfang (TX → RX) auf ZF 1288 MHz decodiert
fehlerfrei (`tests/k56/dvbs_rxcheck.sh`). TS: `tests/k56/make_sat_ts.sh` (NIT satellite_delivery 11,038 GHz/19,2° O/V/22000/1/2).
Sicherheit: Sat-Eingänge führen 13/18 V LNB-Speisung – nie galvanisch an den Pluto (nur Luft oder DC-Block).

**DVB-S mit HD, UHD und HDR (23.10.):** `tests/k56/make_uhd.sh encode|mux3` (UHD: Blender BBB 2160p, HEVC 2160p25 ~11 Mbit/s;
`make_hdr.sh`: Netflix Sol Levante HDR10 (CC BY 4.0) per HTTP-Range aus dem ProRes-Master, HEVC Main10 HDR10 ~11 Mbit/s; mux3: SID 1 HD 0x19,
SID 2 UHD 0x1F, SID 3 HDR 0x1F, CBR 35 480 392 b/s) und `stream_sat_3.sh` (NIT FEC 7/8 mit drei Diensten, EIT je Dienst) → `dvbs_mod --rate 7/8`
(22 MBd: 63 % eines Kerns, Echtzeitfaktor 1,00, Unterlauf 0). Eigenempfang: 1597/1597 RS-Blöcke fehlerfrei, AVC + 2×HEVC im TS.
`dvbs_rx` löst die 180°-Mehrdeutigkeit (invertierte Daten 0x47↔0xB8) jetzt selbst auf.

## dvbs2_mod – DVB-S2 (EN 302 307-1, CCM, ein TS)

`dvbs2_mod -i TS -o iio: --sr 22000000 --mod qpsk|8psk|16apsk|32apsk --rate 3/4 --pilots 1 --rolloff 0.35|0.25|0.20 [--short 1] [--gold N]
--sps 1 --fir rrcXX.fir --lo HZ` – Kodierkette aus `lib/dvbs2.c` (BBFRAME, BCH, LDPC, Bit-Interleaver, Mapping, PL-Framing mit Pilots
und PL-Scrambling), RRC wie bei `dvbs_mod` im AD9361-FIR (`--sps 1`, α = Rolloff) oder in Software (`--sps 2`). Pegel: RMS = A, bei APSK
wird A so begrenzt, dass der Außenring int16 nicht überschreitet. Prüfwerkzeug `dvbs2_stage` (Stufenausgabe, `--selftest`, `--bench`).

Prüfung (23.10., WSL2): `dvbs2_stage --selftest` (schneller Pfad gegen bitweise Referenz, alle MODCODs) OK; `tests/s2_compare_grdtv.py`
BCH- und LDPC-Ausgabe bitgleich mit gr-dtv 3.10 (QPSK, 8PSK, alle Raten); `tests/s2_symbols_all.sh` PLFRAME-Symbole (PLHEADER,
Interleaver, Mapping, Pilots, Scrambling) gleich gr-dtv für alle QPSK-/8PSK-MODCODs mit und ohne Pilots (QPSK 147 dB, 8PSK 98 dB SNR =
Rundung); `tests/s2_mod_vs_stage.sh` `dvbs2_mod` = `dvbs2_stage` Sample für Sample. Dabei gefunden und behoben: MODCOD im PLHEADER
war für 8PSK 5/6, 8/9, 9/10 um eins zu hoch (8PSK hat kein 4/5); 8PSK 4/5 wird jetzt abgelehnt. Hinweis gr-dtv 3.10:
`dvbs2_physical_cc` liefert 2 Samples/Symbol (Nullen dazwischen), Argumentreihenfolge `(framesize, rate, constellation, …)`.

Pluto (A9, ein Kern, `tests/pluto/s2_bench.sh` / `s2_rt.sh`): reine Kodierung QPSK 28,0 / 27,7 / 29,5 MBd (1/2, 3/4, 9/10), 8PSK
19,0–19,7 MBd, 16APSK 3/4 15,8 MBd, 32APSK 8/9 13,7 MBd; mit Ausgabekopie QPSK 3/4 27,6 MBd, 8PSK 3/4 18,6 MBd. Freigegeben sind
deshalb QPSK bis 22 MBd und 8PSK bis 15 MBd. Ausstehend: Empfang am LG (Transponder 11 038 V, ZF 1288 MHz).

**Kleine Symbolraten (DATV):** `dvbs_mod`/`dvbs2_mod --sps 2|4|8` (RRC im Programm, Standard 32·sps Taps), damit z. B. 125/250/333 kS/s
über USB mit ≥ 600 kS/s laufen (AD9364-Minimum ≈ 521 kS/s). `tests/s2_sps_test.sh`: TS bitgleich zurück für sps 2/4/8, QPSK/8PSK,
Roll-off 0,35/0,25/0,20. Kontrollempfänger `tests/dvbs2_hardrx.py` (harte Entscheidung, systematische FEC, CRC-8 je Paket; für
Loopback/hohes SNR), eingebunden in `pc/rxdecode.py` (`decode_dvbs2`); `tests/s2_hardrx_test.sh` mit nachgebildetem Funkweg (CFO,
Phase, Rauschen 25/28 dB).

## Weitere Standards / Bewertung Pfad „N=6912-IFFT + AD9361-FIR“

Idee: OFDM mit N = 6912 Punkten bei 7,714 MS/s erzeugen (gleicher Trägerabstand 1116,07 Hz, alle 6817
Träger) und im AD9361-TX-FIR 2× interpolieren. Rechnung (scipy.remez, 128 Taps, Q15, FIR-Takt 15,43 MHz,
Durchlass 0…3,805 MHz, Sperrbereich ab 3,909 MHz):

| N | f_s (MS/s) | Übergang (kHz) | Welligkeit Passband (dB) | Sperrdämpfung max. (dB) | Bild bei 4,2 MHz (dB) | Bild bei 6 MHz (dB) |
|---|---|---|---|---|---|---|
| 6912 | 7,714 | 104 | 1,31 | −15,7 | −17,2 | −17,0 |
| 7040 | 7,857 | 247 | 0,16 | −34,5 | −37,6 | −34,9 |
| 7168 | 8,000 | 390 | 0,025 | −50,6 | −62,4 | −50,9 |

Bewertung: Mit 128 Taps ist bei N = 6912 nur ≈ 16–17 dB Bildunterdrückung erreichbar – weit unter der
Spektrummaske (nicht-kritisch: −40 dB relativ zum Nutzband bei ±4,2 MHz, −52 dB bei ±6 MHz; kritisch
−50/−62 dB). Erst N = 7168 (8,0 MS/s) würde die nicht-kritische Maske erfüllen, liegt aber mit 32 MB/s
über der gemessenen USB-/iiod-Grenze von 26 MB/s (6,47 MS/s). Der Pfad ist daher für 8-MHz-DVB-T tot.
Für DVB-C bleibt der FIR-Trick wertvoll: 1 Sample/Symbol = 6,9 MBd → 27,6 MB/s (knapp über 26 MB/s),
6,875 MBd → 27,5 MB/s, 6,4 MBd → 25,6 MB/s (ginge). Darum ist auch DVB-C ≥ 6,5 MBd nur on-device
sinnvoll – `dvbc_mod` läuft dort mit `--sps 1` (ein Kern genügt, siehe Messreihe).

## Lizenzen

Eigener Code: MIT (`LICENSE`). Es wurde **kein** Code aus gr-dtv (GPL-3.0) oder libdvbmod übernommen; die
Normtabellen wurden gegen gr-dtv gegengeprüft. Die Prüfskripte `tests/dvbt_rx_grdtv.py` und
`tests/dvbt_tx_grdtv.py` nutzen gr-dtv zur Laufzeit (GPL-3.0-or-later) – wer sie weitergibt, tut das unter
GPL-3.0. reedsolo (MIT), numpy/scipy (BSD), TSDuck (BSD-2-Clause) werden nur als Werkzeuge genutzt.
Das AD9361-FIR-Dateiformat folgt der ADI-Dokumentation (libad9361-iio LGPL-2.1, Treiber GPL-2.0), ohne Codeübernahme.

## Offene Punkte

* Pluto: DAC-Lauf auf Werksfirmware v0.31 steht aus (dablab-Build: bestanden, siehe oben). `out_write` schreibt direkt in den IIO-DMA-Puffer; Unterläufe
  werden über `iio_buffer_push`-Fehler gezählt, ein Kernel-Zähler fehlt in v0.31.
* Echtzeit braucht `--affinity --rt`; die Firmware sollte `dvbt_mod` so starten (oder `chrt -f 40 taskset`).
* Weitere Reserve möglich: Rahmenaufbau (112 µs) und Symbol-Interleaver (59 µs) zu einem Scatter zusammenlegen,
  FEC (418 µs) mit NEON-RS (vtbl) – nicht mehr nötig für das Ziel, aber für DVB-C parallel auf demselben Gerät.
* Der Statuszähler `clipped` ist nicht implementiert (immer 0); den Clip-Anteil offline messen (`iq_analyze.py`).
* `tests/dvbt_analyze.py` (numpy): Synchronisation, Pilotfolge, TPS-Decodierung für Mitschnitte; gr-dtv stürzt auf echten Pluto-Mitschnitten teils ab (Heap-Korruption beim Beenden).
* Spektrum am Analysator nachmessen (Fensterung + TX-FIR); 2k braucht das TX-FIR für ≤ −45 dBc.
* UDP-Eingang und `--realtime` sind mit einer TSDuck-Quelle (tsp regulate → udp://127.0.0.1) 20 s in Echtzeit
  getestet (`tests/results/udp_realtime_x86.json`: 0 verworfene Pakete, Stopfen nur beim Anlauf, Füllstand stabil);
  ein Langzeittest über Stunden und ein Vorlauf (erst starten, wenn der Ring zu 25 % gefüllt ist) stehen aus.
* DVB-C 1-Sample/Symbol über den AD9361-FIR ist numerisch (numpy-Nachbildung) verifiziert, nicht am Gerät.
* Hierarchische Modulation, 4k-Modus und in-depth-Interleaver (DVB-H) sind nicht implementiert.
* gr-dtv-Prüfung lief nur mit nicht-hierarchischen Modi; gr-dtv ignoriert die LP-Coderate-Bits, die Norm-Variante
  (000) ist Standard, `--tps-lp-same` als Alternative vorhanden.

## Ergebnisübersicht (tests/results/summary.json, x86, Stand mit Standardfenster)

| Test | Fenster | bitexakt | Pakete | Bitfehler | RT x86 | PAPR (dB) | Schulter (dBc) |
|---|---|---|---|---|---|---|---|
| dvbt_8k_64qam_2-3_1-32_8MHz | 32 | ja | 2244 | 0 | 4.033 | 11.9 | -64.1 |
| dvbt_8k_64qam_7-8_1-4_8MHz | 32 | ja | 1152 | 0 | 4.954 | 11.5 | -64.0 |
| dvbt_8k_16qam_3-4_1-8_8MHz | 32 | ja | 2244 | 0 | 4.552 | 11.7 | -64.7 |
| dvbt_8k_qpsk_1-2_1-16_8MHz | 32 | ja | 4260 | 0 | 4.085 | 12.1 | -63.7 |
| dvbt_2k_64qam_5-6_1-32_8MHz | 8 | ja | 4004 | 0 | 3.744 | 12.0 | -38.7 |
| dvbt_2k_16qam_2-3_1-4_8MHz | 8 | ja | 4576 | 0 | 5.544 | 12.1 | -39.1 |
| dvbt_2k_qpsk_7-8_1-8_8MHz | 8 | ja | 4704 | 0 | 5.029 | 11.8 | -38.4 |
| dvbt_8k_64qam_2-3_1-32_6MHz | 32 | ja | 2244 | 0 | 4.929 | 11.9 | -64.8 |
| dvbt_8k_64qam_2-3_1-32_7MHz | 32 | ja | 2244 | 0 | 4.708 | 11.9 | -64.8 |
| dvbc_16qam_sps2 | - | ja | 968 | 0 | 1.567 | 7.4 | -70.3 |
| dvbc_16qam_sps1 | - | ja | 968 | 0 | 8.889 | 7.4 | -70.3 |
| dvbc_32qam_sps2 | - | ja | 1208 | 0 | 1.409 | 6.6 | -70.3 |
| dvbc_32qam_sps1 | - | ja | 1208 | 0 | 6.925 | 6.6 | -70.3 |
| dvbc_64qam_sps2 | - | ja | 1456 | 0 | 1.469 | 7.6 | -70.3 |
| dvbc_64qam_sps1 | - | ja | 1456 | 0 | 6.539 | 7.6 | -70.3 |
| dvbc_128qam_sps2 | - | ja | 1704 | 0 | 1.293 | 7.4 | -70.3 |
| dvbc_128qam_sps1 | - | ja | 1704 | 0 | 5.64 | 7.4 | -70.3 |
| dvbc_256qam_sps2 | - | ja | 1944 | 0 | 1.437 | 8.1 | -70.3 |
| dvbc_256qam_sps1 | - | ja | 1944 | 0 | 5.372 | 8.1 | -70.3 |

8k mit Standardfenster W=32: Schulter −64 dBc; 2k mit W=8: −39 dBc (für ≤ −45 dBc zusätzlich das AD9361-TX-FIR, siehe „Spektrum“). DVB-C: RRC-Formung, −70 dBc.
