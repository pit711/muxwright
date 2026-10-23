# DAB-Lab Runner (`dablab_run.py`)

Erzeugt aus einer JSON-Konfiguration (Schema siehe `../SPEC.md`) ein DAB/DAB+-Ensemble und gibt es als IQ-Strom aus:

```
ffmpeg (Datei/Testsignal/Playlist) → odr-audioenc (+ odr-padenc: DLS, DL Plus, MOT-Slideshow) → FIFO
   → odr-dabmux (FIFO blockierend gelesen, ETI an Pipe, Fernsteuerung telnet 127.0.0.1:12721)
   → odr-dabmod (ini: 4,096 MS/s, s16, FIR, TII, CFR, Fernsteuerung :12722) → Python-Relais → stdout
```

Die Kette wird vom Leser getaktet, am PC also vom Pluto: Jede Stufe schreibt blockierend, nirgends läuft ein
Echtzeit-Schrittmacher. Liest niemand, steht alles still. Liest der Leser in Echtzeit, liefert die Kette genau 4,096 MS/s.

Nur Python-Standardbibliothek (getestet mit 3.9 und 3.12). `numpy` wird verwendet, wenn es vorhanden ist.
Nötig ist es für `iqconv` und `--shift-hz`, außerdem beschleunigt es Pegelmessung und cf32-Umwandlung.
Lauffähig unter macOS (BSD) und Linux/WSL1 (GNU). Der Runner ruft keine Shell-Werkzeuge auf, nur die ODR-Programme, ffmpeg, magick und welle-cli.

## Verzeichnisse und Umgebung

| Variable | Bedeutung | Standard |
|---|---|---|
| `DABLAB_BIN` | Ordner mit `odr-audioenc`, `odr-padenc`, `odr-dabmux`, `odr-dabmod`, `welle-cli` | Ordner von `odr-dabmux` im `PATH`, sonst `/opt/dablab/bin`, `~/dablab/prefix/bin` |
| `DABLAB_RUNTIME` | Laufzeitordner: `media/`, `sessions/<id>/`, `checks/`, `cache/` | Skriptordner, wenn er `runtime` heißt, sonst `<Skriptordner>/../runtime` |
| `DABLAB_TMP` | Ordner für FIFOs. Muss ein echtes Unix-Dateisystem sein, **nicht** `/mnt/g` (DrvFs kennt keine FIFOs) | `/tmp` |

`ffmpeg` und `magick` bzw. `convert` werden in `DABLAB_BIN`, im `PATH`, unter `/opt/homebrew/bin` und unter `/usr/bin` gesucht.
Pfade in der Konfiguration wie `media/x.jpg` sind relativ zu `DABLAB_RUNTIME`.

Aufruf am PC (WSL1, Distribution `dablab`, Werkzeuge in `/opt/dablab/bin`):
`wsl -d dablab -- bash /mnt/<drive>/.../dablab/runner/tests/wsl_env.sh python3 dablab_run.py <befehl> ...`
`tests/wsl_env.sh` setzt `DABLAB_BIN=/opt/dablab/bin`, `DABLAB_RUNTIME=/mnt/<drive>/.../dablab/runtime` und einen sauberen
`PATH`. Unabhängig davon entfernt der Runner selbst alle `/mnt/...`-Einträge aus dem `PATH` (WSL hängt den Windows-PATH an,
sonst könnte ein Windows-ffmpeg greifen) und stellt `DABLAB_BIN` voran. Aus Git-Bash heraus `MSYS_NO_PATHCONV=1` setzen.
FIFOs, PAD-Sockets und die IQ-Dateien von `check` liegen in `/tmp`, weil `/mnt/g` langsam ist und keine FIFOs kennt.
Medien, Playlists, Sitzungsordner und Prüfberichte liegen in `DABLAB_RUNTIME`. Der Mac ist der Ausweichweg (`deploy_mac.sh`).

## Befehle

| Befehl | Wirkung / Ausgabe |
|---|---|
| `live --config c.json [--shift-hz N]` | Startet die Kette. **stdout**: int16 LE I/Q mit `rf.sample_rate`. **stderr**: erste Zeile `{"session":"<id>"}`, danach JSON-Zeilen `{"t","lvl","msg",...}`, alle 5 s eine Statuszeile mit Rate und Pegel. Ende bei stdout-EOF/EPIPE, SIGTERM, SIGINT oder SIGHUP. Alle Kindprozesse, FIFOs und PAD-Sockets werden aufgeräumt. |
| `render --config c.json --seconds N --out f [--rate 2048000] [--format cf32\|s16]` | Rendert N s (die ersten 0,5 s Einschwingzeit werden verworfen). Gibt eine JSON-Zusammenfassung aus mit Pegel, `tx_fic` (FIC aus dem ETI-Abgriff) und `data_services` (Prüfung der Paketströme im ETI). |
| `check --config c.json [--seconds 20] [--wait 40] [--keep]` | `render` mit 2,048 MS/s cf32, dann welle-cli `-D -w <freier Port> -f x.cf32.iq`. Liefert den Prüfbericht (s. u.) mit Soll/Ist-Vergleich `mismatches`. |
| `decode --iq f.cf32.iq [--seconds 20] [--config c.json] [--mode 1-4]` | Dekodiert einen Mitschnitt (RX-Kontrolle). Mit `--config` gibt es zusätzlich einen Soll/Ist-Vergleich. |
| `ctl --session <id> '<json>'` | Änderungen zur Laufzeit, Ausgabe `{"ok":true,...}`, siehe unten |
| `selftest [--quick]` | Prüft Werkzeuge und Versionen, FIFO und Unix-Sockets, Medien, CU-Rechnung, MOT-Encoder, DL Plus, den FIFO-Patch und die Pegelkalibrierung für alle Modi und Raten |
| `validate --config c.json` | Nur Prüfung: Fehler, Warnungen, CU-Bilanz |
| `iqconv --in f.s16 --in-rate 4096000 --out g.cf32.iq` | Wandelt s16 in cf32 und dezimiert dabei (Halbband-FIR, 2:1). Damit wird ein Live-Mitschnitt dekodierbar (braucht numpy). |
| `mkmedia` | Legt Beispielmedien an: `media/slide1.jpg`, `slide2.jpg`, `logo.png`, `logo_320x240.png` |
| `playlist list \| show <name> \| import <datei.m3u\|.m3u8\|.pls> [name] \| export <name> [datei.m3u8]` | Verwaltet Playlists in `runtime/playlists/<name>.json`. Beim Import werden relative Pfade auf `media/` bezogen, `#EXTINF` bzw. `TitleN` werden in Interpret und Titel zerlegt. Der Export erzeugt M3U8 mit `#EXTINF` |
| `media youtube <url> \| list \| info \| channels \| rates \| tools` | DVB-Medien und Tabellen (siehe Abschnitt DVB unten). `live/render/check/validate/ctl` mit `mode: dvbt\|dvbc` gehen an das Paket `dvb/` |

### ctl

```
{"dls":{"service":"srv1","text":"...","dlplus":{"artist":"..","title":".."},"rotate":false}}
{"slide":{"service":"srv1","file":"media/x.jpg","mode":"replace|add"}}
{"announcement":{"cluster":1,"active":true}}          → DabMux: set ann_<id> active 0/1 (FIG 0/19)
{"label":{"service":"srv1","label":"..","shortlabel":".."}}   → DabMux: set srv_<key> label L,S
{"pty":{"service":"srv1","pty":10}}                    {"tii":{"enable":1,"comb":1,"pattern":11}}
{"gain":{"digital":1.1}}                               {"raw":{"target":"mux|mod","cmd":"show ofdm"}}
{"playlist":{"service":"srv1","action":"next|prev|jump","index":n}}
```
Mehrere Schlüssel in einem JSON sind erlaubt. Nach einer DLS-Änderung per ctl stoppt eine laufende Rotation
(`"rotate":true` setzt sie fort). Der Zustand liegt in `sessions/<id>/`: `session.json` mit Ports und PID,
`dls_<key>.txt`, `slides_<key>/`, Logs aller Prozesse sowie `dablab.mux` und `dabmod.ini`.
Wenn 12721 belegt ist, nimmt die Mux-Fernsteuerung einen freien Port, `ctl` liest ihn aus `session.json`.

**Latenz von ctl-Änderungen**: Gemessen auf dem Mac erschienen DLS, Label und Slide in dem Abschnitt, der 8 s nach dem ctl
aufgenommen wurde. Die Verzögerung entsteht in den Puffern der FIFOs und Pipes. Unter Linux verkleinert der Runner sie
mit `F_SETPIPE_SZ`. Dazu kommen die DLS-Einfügerate von PadEnc (1,2 s) und das Slide-Intervall.

## Konfiguration → Werkzeuge (was umgesetzt ist)

| Schema | Umsetzung |
|---|---|
| `mode` 1–4 | `dabmode` (Mux), `mode` (Mod). Bei Modus III wird der FIC-Scheduler `priority` verwendet, weil `classic` in ODR-DabMux 5.5.1 dort abbricht („invalid fib“) |
| `ensemble.*` | id, ecc, label, shortlabel, `international-table`, `local-time-offset` (auto oder `lto`/2 h), `reconfig-counter` (hash/Zahl/off). `charset: "utf8"` erzeugt zusätzlich FIG-2-Labels (UTF-8), FIG 1 bleibt EBU Latin. `alarm_flag: true` legt einen inaktiven Alarm-Cluster 0xFF an, dadurch setzt ODR das AL-Flag in FIG 0/0 |
| `datetime` | FIG 0/10 wird von ODR-DabMux immer gesendet. `false` erzeugt nur eine Warnung |
| `services[].type` | `dabplus` (AAC-LC `--aaclc`, HE-AAC `--sbr`, HE-AACv2 `--ps`, 48/32 kHz, Mono/Stereo, 8–192 kbit/s), `dab` (MP2 `-a --dabmode=s/j/d/m`, 48/24 kHz), `data` (Paketmodus) |
| `protection` | EEP_A 1–4, EEP_B 1–4 (Bitrate ein Vielfaches von 32), UEP 1–5 (nur MP2, Tabelle 6) |
| `pty`, `pty_dynamic`, `language`, `ecc` | FIG 0/17 (S/D), FIG 0/5, FIG 0/9 |
| `audio.source` | ffmpeg: Datei (`-stream_loop -1`; bei `loop:false` folgt Stille), `tone`, `silence`, `noise`, `pinknoise`, `sweep` (logarithmisch, periodisch), `lr_test` (L/R getrennte Frequenzen), `polarity` (asymmetrischer, gleichanteilfreier Puls auf beiden Kanälen). `level_dbfs` gilt exakt für Ton/Sweep/LR/Polarität und näherungsweise für Rauschen |
| `audio.source.kind = "playlist"` | Ein Python-Thread (`PlaylistFeeder`) startet je Titel ein ffmpeg (alle ffmpeg-Formate: mp3, ogg, opus, flac, wav, m4a, wma), Ausgabe s16le mit 48 kHz bzw. der Dienstrate. Alle Titel laufen nacheinander in **dieselbe** PCM-Pipe des Encoders, also nahtlos und sample-genau. `normalize` → `loudnorm=I=-23:TP=-1.5:LRA=11` (EBU R128), `shuffle` mischt bei jedem Durchlauf neu, `loop:false` → nach dem letzten Titel Stille. Titel und Interpret kommen aus der Playlist, fehlende Angaben aus den Tags (ffprobe), notfalls aus dem Dateinamen. `dls_from_tags` setzt bei jedem Titelwechsel DLS „Interpret - Titel“ mit DL Plus ITEM.ARTIST/ITEM.TITLE (ein manuelles ctl-DLS hat Vorrang). `now_playing` erscheint als eigene stderr-Zeile beim Wechsel und in jeder Statuszeile, zusätzlich in `sessions/<id>/now_playing_<key>.json`. `crossfade_s` wird nicht unterstützt, es gibt nur eine Warnung |
| `audio.gain_db`, `bandwidth_hz` | `odr-audioenc -g`, `-B` |
| `pad.dls` | `odr-padenc -t`. DL Plus über den Parameterblock der DLS-Datei (Tags artist/title/album/station/program/url; fehlt ein Wert im Text, wird er angehängt). `rotate`/`rotate_s` übernimmt ein Thread im Runner. `charset: utf8` → `-C -c 15`, `remove_label` → `-r` |
| `pad.sls` | `odr-padenc -d <Sitzungsordner> -s interval -m max_bytes [-R]`, FIG 0/13 „slideshow“. `categories` (CatSLS) kann ODR-PadEnc nicht |
| `announcements` (ASu) | FIG 0/18 je Dienst. `Rad_info` wird auf `ProgrammeInfo` abgebildet |
| `clusters` (ASw) | FIG 0/19, Cluster-uid `ann_<id>`, live schaltbar per ctl |
| `linking.fm_pi` / `fm_freqs_mhz` / `other_ensembles` | FIG 0/6 (hart, LSN = SId & 0xFFF), FIG 0/21 (FM, dazu die eigene DAB-Frequenz), FIG 0/24. In diesem Fall wird automatisch der FIC-Scheduler `priority` verwendet, siehe Grenzen |
| `data` (Paketmodus) | Eigener MOT-Encoder in Python (Datengruppen und Pakete mit CRC). `slideshow_packet`/`raw`: MOT-Header-Modus. `spi`: MOT-Directory-Modus mit den Dateien und `spi.logo`. `tpeg`/`journaline`: eine fertige Paketstrom-Datei wird durchgereicht. Paketgröße ≤ Bitrate × 3 Byte je 24-ms-Rahmen |
| `tii` | `[tii]` in dabmod.ini (comb, pattern, old_variant), per ctl änderbar |
| `rf.sample_rate`, `backoff_db` | `rate`, `digital_gain` aus einer Kalibriermessung (RMS bei Gain 1, je Modus und Rate gemessen, gespeichert in `cache/calib.json`). Ziel: RMS = −backoff dBFS |
| `rf.cfr` | `[cfr] enabled=1`, `clip` = √Träger × 10^((backoff−0,5)/20), einstellbar über `rf.cfr_clip`, `rf.cfr_papr_db`, `rf.cfr_error_clip` |
| `rf.channel/freq_hz/offset_hz/lo_offset_hz/atten_db/enabled` | Sache des PCs (Pluto). Der Runner benutzt `freq_hz` nur für FIG 0/21. Mit `live --shift-hz` kann er digital verschieben (numpy) |
| Kapazität | Die Summe der CU (EEP-A 12/8/6/4·n, EEP-B 27/21/18/15·n, UEP nach Tabelle) wird vor dem Start gegen 864 CU geprüft. Bei Überschreitung gibt es einen klaren Fehler mit der Aufschlüsselung je Dienst |

## Prüfbericht (`check`/`decode`)

Felder nach SPEC: `ok, sync, snr_db, freq_corr_hz, ensemble{eid,label,shortlabel,ecc,lto,datetime,al_flag,...}`,
`services[{sid,label,shortlabel,pty,pty_dynamic,language,codec,mode,bitrate,protection,samplerate,channels,dls,dlplus[],
slides[{file,w,h,bytes}],audio_level_db[L,R],errors{frame,rs,aac,xpad},announcements(FIG 0/18),user_apps}]`,
`tii[]`, `announcements[]` (FIG 0/19 aus der empfangenen FIC), `linking{fig0_6,fig0_21,fig0_24}`, `packet_components`,
`fic` (FIB-, CRC- und FIG-Zähler), `raw{"mux.json"}`. Nur bei `check` kommen hinzu: `tx_fic` (FIC des gesendeten ETI
über die volle Dauer), `render`, `capacity`, `mismatches`.

Woher die Werte stammen: Aus welle-cli `/mux.json` kommen Labels, Codec, DLS, Pegel, Fehler und TII. Aus dem `/fic`-Strom von
welle-cli, den ein eigener FIG-Parser liest, kommen AL-Flag, LTO, Datum, PTy S/D, Sprache, ASu, ASw, Linking, FI, OE,
Paketkomponenten und User-Apps. Die Slides kommen aus `/slide/<sid>`. Gewartet wird, bis alle erwarteten DLS, DL Plus,
Slides und TII da sind, höchstens `--wait` s. welle-cli spielt die Datei in einer Schleife ab. Jeder Neustart der Schleife
kostet 1–2 RS-Fehler je Dienst, die Fehlertoleranz im Soll/Ist-Vergleich wächst deshalb mit der Zahl der Durchläufe.

## Änderungen an den Quellen (Patches, `patches/`)

Die Patches sind idempotent. Aufruf: `python3 patches/patch_dabmux.py <ODR-DabMux-Ordner>`,
`python3 patches/patch_audioenc.py <ODR-AudioEnc-Ordner>`, `python3 patches/patch_welle.py <welle.io-Ordner>`, danach neu bauen
(`make && make install` bzw. in `welle.io/build`: `make welle-cli` und die Datei nach `bin/` kopieren).

1. **ODR-DabMux `input/File.cpp`, `mpeg.c` [DABLAB_FIFO]**: `read()` auf eine FIFO liefert oft weniger als einen Rahmen.
   ODR wertete das als Dateiende und versuchte `lseek` („Can't rewind“). Danach waren DAB+-Superframes verschoben. Jetzt wird
   blockierend nachgelesen, bis der Rahmen vollständig ist oder echtes EOF kommt. Ohne diesen Patch läuft die geforderte Kette
   (FIFO, blockierend) nicht zuverlässig. `selftest` prüft das mit einem Schreiber, der in 333-Byte-Häppchen schreibt.
2. **ODR-DabMux `ConfigParser.cpp` [DABLAB_LTO]**: Ein negativer `local-time-offset` verlor sein Vorzeichen (`abs()`).
3. **welle-cli [DABLAB_DLPLUS]**: Der DL-Plus-Befehl (DLS-Kommando 0x02) wurde verworfen. Jetzt erscheint er als Hex in
   `/mux.json` → `services[].dls.dlplus`, der Runner dekodiert ihn zu Tags mit Text.
4. **welle-cli [DABLAB_MODE]**: Mit der Umgebungsvariable `WELLE_DAB_MODE=1..4` lassen sich Modi II–IV einstellen
   (welle-cli kannte nur Modus I). Der Runner setzt sie aus der Konfiguration.

5. **ODR-AudioEnc `SampleQueue.h` [DABLAB_QUEUE]** (`patches/patch_audioenc.py`): `pop_wait()` wartete in einer do-while-Schleife
   **immer** mindestens einmal 10 ms, auch wenn genug Samples bereitlagen. Der Encoder war dadurch auf etwa einen Rahmen je 10 ms
   gedrosselt. Unter WSL1 (Timer-Raster 15,6 ms) schaffte AAC-LC nur 1,28-fache Echtzeit, die ganze Kette ebenso. **Das war
   der Durchsatz-Engpass unter WSL.** Nach dem Fix läuft der Encoder mit mehr als 100-facher Echtzeit.
6. **ODR-AudioEnc `PadInterface.cpp` [DABLAB_PADSYNC]**: Bis zu 40 ms wird auf die Antwort von PadEnc gewartet statt sofort
   weiterzumachen. Sonst fehlte PAD in vielen AUs, sobald der Encoder schneller als Echtzeit läuft (render/check).
7. **welle-cli `input/raw_file.cpp` [DABLAB_RAWFILE]**: Puffer 256 KiB → 16 MiB, Wartezeit des Lesers 100 ms → 1 ms. Unter WSL1
   spielte welle-cli Dateien sonst weit langsamer als in Echtzeit ab (eine 15-s-Datei war nach 40 s noch nicht durch). Slides und DLS
   kamen deshalb in `check` nicht an. Nach dem Patch: 4 Durchläufe in 40 s.

Alle sieben Patches sind auf dem Mac (`~/dablab/src`) und in WSL (`/opt/dablab/src`) angewendet, die Programme neu gebaut.

Die schon vorher vorhandenen macOS-Build-Patches (`~/dablab/macpatch*.py`) gehören nicht zu diesem Teil.

## Pegel (gemessen, Mac, 4,096 MS/s, 5 s, vollständig ausgewertet)

| Variante | RMS dBFS | Spitze dBFS | Crest dB | geclippte Werte |
|---|---|---|---|---|
| backoff 15 | −15,00 | −3,12 | 11,9 | 0 |
| backoff 12 (Standard) | −12,00 | +0,20 | 12,2 | 1 von 41 Mio. |
| backoff 9 | −9,00 | +2,45 | 11,5 | 2934 |
| backoff 12 + CFR | −11,99 | +0,09 | 12,1 | 0 |

Kalibrierung: RMS bei `digital_gain=1` liegt bei −12,85 dBFS (Modus I), −12,95 (II) und −12,88 (IV). Die Abweichung zwischen 2,048 und 4,096 MS/s ist kleiner als 0,02 dB.
Das 99,99-%-Quantil liegt 9,7 dB über dem RMS. Empfehlung: `backoff_db` ≥ 12. Das Relais misst laufend RMS, Spitze und Clips, die Werte stehen in der Statuszeile.

## Grenzen und offene Punkte

- **Modi II–IV**: ODR erzeugt alle vier Modi. welle-cli 2.7 dekodiert aber nur Modus I vollständig. In Modus II kommen FIC und Labels an, die MSC dagegen mit sehr vielen RS-Fehlern (welle-intern). Modus III und IV dekodiert es gar nicht. Prüfen ließe sich das mit einem anderen Empfänger (Qt-DAB/dablin) oder einem echten Radio.
- **CFR**: Die CFR von odr-dabmod 3.2.1 clippt zwar (laut Fernsteuerung 1,85 % der Samples), stellt die In-Band-Träger aber wieder her. Gemessen sank die PAPR nur von 12,15 auf 12,09 dB. Der Schalter ist umgesetzt, wirksam ist er praktisch nicht.
- **FIG 0/6, 0/21, 0/24** haben in ODR die Rate E (alle 120 s). Mit dem Scheduler `classic` gehen sie bei vollem FIC frühestens nach etwa 2 min erstmals raus. Der Runner nimmt deshalb `priority`, sobald Linking konfiguriert ist; damit kommen sie gleich am Anfang. Überschreiben lässt sich das mit `"fic_scheduler"` in der Konfiguration. ODR-DabMux kodiert FM-Frequenzen abgeschnitten statt gerundet (87,6 → 87,5). Der Runner gleicht das mit +4 kHz in der Mux-Datei aus.
- **SPI/Logos**: Der Paketmodus-Kanal mit MOT-Directory, Logo-Dateien und FIG 0/13 „SPI“ wird gesendet. Im ETI ist er geprüft: CRC und vollständige MOT-Objekte. Es fehlt das binär kodierte SI-Dokument nach ETSI TS 102 371, ohne das ein Empfänger die Logos keinem Dienst zuordnet. Dafür bräuchte man einen Encoder wie `hybridspi`/`python-dabepg` (Opendigitalradio). Die Logos müssen außerdem ContentNames nach TS 102 818 tragen (z. B. `<sid>/logo_colour_square`) und ein Scope-Parameter muss gesetzt sein. Ein bereits kodiertes SI-Dokument kann man in `data.files` mitgeben. welle-cli dekodiert keinen Paketmodus, die Prüfung endet deshalb auf ETI-Ebene (`data_services[].eti_check`).
- **TPEG/Journaline**: Es gibt keinen Encoder. Ein fertiger Paketstrom wird nur durchgereicht.
- **TII alte Variante** (`old_variant`): ODR sendet sie, welle-cli erkennt sie nicht. Die neue Variante wird erkannt (getestet: 1/11, 5/3, 12/40).
- **CatSLS** (`sls.categories`) kann ODR-PadEnc nicht.
- **Kurzlabel**: Nach ODR-Regel dürfen es höchstens 8 Zeichen sein, und zwar Zeichen des Labels in derselben Reihenfolge. Das wird vorab geprüft.
- **Durchsatz** (WSL1, Ryzen 5 3600, gemessen unter Windows mit `wsl ... live | python tests/win_reader.py`): Ohne Bremse
  liefert die Kette 189 MB/s mit drei Diensten (11,5-fache Echtzeit, Minimum 38,7 MS/s je Sekunde) bzw. 198 MB/s mit
  Standard 6C. Mit Echtzeit-Leser kommen 4 095 999 S/s, der Rückstand bleibt unter 8 ms. Ohne DABLAB_QUEUE waren es nur 1,29-fache
  Echtzeit. odr-dabmod allein schafft 14-fache Echtzeit bei 4,096 MS/s.
- `render` läuft gebremst mit 3-facher Echtzeit (`DABLAB_RENDER_SPEED`), weil PadEnc nach der Wanduhr arbeitet (DLS-Einfügung).
  Für `check` werden Slides alle 1 s eingefügt. `-s 0` würde in ODR-PadEnc das DLS ganz verdrängen.
- Latenz: Unter Linux verkleinert der Runner den FIFO zwischen Encoder und Mux auf etwa 0,25 s Audio. Auf dem Mac geht das nicht, dort puffert der FIFO je nach Bitrate einige Sekunden.

## Tests (`tests/`)

- `live_test.py --config X --seconds 30 --outdir D [--sigterm]`: Echtzeit-Leser mit 16,384 MB/s, nach 12 s ctl (DLS, Durchsage, Slide, Label), danach 10 s langsamer Leser mit 25 %. Ende durch stdout-EOF oder SIGTERM, danach Suche nach übrigen Prozessen, Zombies und FIFOs. Die Abschnitte A (4–12 s) und B (20–30 s) werden mit `iqconv` und `decode` ausgewertet.
- `stuck_reader_test.sh`: SIGTERM, während der Leser angehalten ist (SIGSTOP).
- `matrix_test.py`: Funktionsmatrix aus Codecs, Raten, UEP/EEP, Modi, UTF-8/LTO, TII, CFR, Paketmodus/SPI, Quellen und Überlast.
- `level_test.py`: Backoff und CFR, Pegel und Clips.
- `summarize.py`: Kurzfassung eines Prüfberichts.

- `playlist_ctl_test.py`: live mit Playlist, ctl next/jump/prev, `now_playing`-Verlauf.
- `make_playlist_media.sh`: zwei getaggte Testtitel (MP3/OGG) und die Playlist `testliste` (per M3U-Import).
- `win_reader.py`: Durchsatz unter Windows lesen, wahlweise mit 16,384 MB/s (`--paced`) oder unbegrenzt.
- `perf_*.sh`, `welle_speed.sh`, `pad_variants.py`: Messungen der Einzelstufen und Diagnose.

Ergebnisse der Abnahme: **WSL `tests/results_wsl/`** (maßgeblich), Mac `tests/results_mac/` (vor den Patches 5–7).

## UKW-Testsender (`mode: "fm"`, Paket `fm/`)

Bei `mode: "fm"` delegiert `dablab_run.py` die Befehle `live`, `render`, `check`, `decode`, `ctl`, `validate` an `fm/fm_tx.py`
(Haken: `_fm_dispatch()`, ctl-Weiche über `session.json` → `"mode":"fm"`, `selftest` bekommt den Abschnitt `fm`). Die
DAB-Logik bleibt unverändert. Wiederverwendet werden die Audioquellen (`ffmpeg_cmd`, `PlaylistFeeder`), Protokollierung,
Sitzungsordner, `level_stats`, `halfband_taps`. Konfigurationsschema: `../SPEC.md`, Abschnitt „UKW“.

```
ffmpeg/Playlist (48 kHz s16le Stereo) ──► fm/dsp.py je Träger:
  Preemphasis 50/75 µs (FIR, Betragsgang exakt) → Limiter (Blockverstärkung, Halbcosinus-Rampe, Clipper) → 15-kHz-FIR
  → ×19/4 auf 228 kHz → MPX [kHz]: dev·((L+R)/2 + (L−R)/2·sin38) + Pilot·sin19 + RDS·biphase·{sin|cos}57
  → ×1024/57 auf 4,096 MS/s (zustandsbehafteter Polyphasen-Resampler) → Phasenakkumulator → cos/sin (float32)
[+ zweiter Träger: Frequenzversatz wird dem Hub zugeschlagen, keine Mischstufe] → a1·IQ1 + a2·IQ2 → int16 I/Q → stdout
```

Blocklänge 18240 MPX-Samples (80 ms) = 95 RDS-Bits = 327680 Ausgangssamples; alle Filter (`lfilter zi`, Resampler-Historie,
RDS-Overlap-Add, Phasenakkumulator) tragen ihren Zustand über die Blockgrenze. RDS-Bitebene in `fm/rds.py` (CRC, Offsetwörter
inkl. C′, Gruppen 0A/0B, 1A, 2A/2B, 3A, 4A, 10A, 11A, 14A/14B, 15A, Zeichensatz EN 50067 Annex E, AF Methode A/B,
Differenzcodierung, Wiederholraten nach MiniRDS). Vorlagen und Lizenzen: `fm/NOTICE.md`.

| Befehl | UKW-Besonderheit |
|---|---|
| `live --config fm.json [--shift-hz N]` | wie DAB; Statuszeile zusätzlich mit `cpu_pct`, `carriers[]` (MPX-Spitze, MPX-Leistung dBr, Limiter, RDS-Gruppenzähler, CT), `amplitudes_dbfs` |
| `render --config fm.json --seconds N --out f [--rate 4096000\|2048000] [--format s16\|cf32]` | 2,048 MS/s per Halbband-Dezimation. In `render`/`check` wird nach ~2 s eine CT-Gruppe eingefügt (`immediate_ct`), sonst nur an der Minutenkante |
| `check --config fm.json [--seconds 20] [--keep]` | render s16 4,096 MS/s nach `/tmp` → `fm/fm_check.py` (Demodulation, Messungen, redsea) → Bericht, Kopie in `runtime/checks/fmcheck-*.json` |
| `decode --iq f [--config c] [--format s16\|cf32] [--rate N] [--shift-hz N] [--mode fm]` | ohne `--config`: Auto-Erkennung UKW (≥ 85 % der Leistung in ±150 kHz). Standard: `.s16` = 4,096 MS/s, sonst cf32 2,048 MS/s (Pluto-RX-Mitschnitt des PC-Servers) |
| `ctl --session <id> '<json>'` | Kommandodatei `sessions/<id>/ctl_*.json`, der Prozess übernimmt sie an der nächsten Blockgrenze (≤ 0,4 s) und schreibt `ack_*.json`; `ctl` wartet bis 4 s auf die Bestätigung. Schlüssel siehe SPEC („ctl (UKW)“) |
| `selftest` | Abschnitt `fm`: RDS-Bitebene (120 Gruppen gegen Syndrome), DSP-Blockkontinuität, redsea-Version, interne Schleife (4 s erzeugen → demodulieren → Pilot/Hub/RDS → redsea PI/PS) |

Werkzeug: **redsea 1.3.1** (MIT) in WSL aus GitHub gebaut (`/opt/dablab/src/redsea`, meson/ninja, liquid-dsp 1.6, libsndfile,
nlohmann-json), Binary `/opt/dablab/bin/redsea`; Aufruf `redsea --input mpx -r 256000 -c 1 -E -p` mit dem demodulierten MPX
als int16. redsea gibt den RDS-Code 0x8D als „β“ aus (EN 50067: „ß“); der Vergleich setzt beide gleich.

Beispiele: `examples/fm_standard.json` (100,0 MHz Stereo + RDS, 1 kHz −0,5 dBFS), `fm_mono.json`, `fm_rds_voll.json` (RT+, EON,
CT, PTYN, LPS, AF Methode B, 1A mit LIC/PIN, L/R-Test 1 kHz/400 Hz), `fm_nachbarkanal.json` (Störer +200 kHz −10 dB, beide mit RDS),
`fm_pegelrampe.json` (Mono 1 kHz 40 % Hub für die Empfindlichkeitsrampe des PC-Servers `/api/sweep`).

Tests: `tests/fm_checks.sh` (alle Beispiele 20 s), `tests/fm_live_test.py` (60 s Echtzeit-Leser, ctl bei 30 s, Abschnitte A/B
dekodieren, CPU-Anteil, Kindprozesse), `tests/fm_summarize.py`. Ergebnisse: `tests/results_wsl/check_fm_*.json`,
`fm_live_standard.json`.

### UKW-Abnahme (WSL1, Ryzen 5 3600, 23.10.2026, `tests/results_wsl/`)

| Prüfung | Soll | Ist |
|---|---|---|
| `check fm_standard` 20 s: Hub gesamt (Spitze) | 75 kHz ±2 % | **74,02 kHz** (−1,3 %; 1 kHz −0,5 dBFS +0,42 dB Preemphasis; Audioanteil 65,66 kHz, Soll 65,67) |
| Pilot | 6,75 kHz ±5 %, 19 000 Hz ±2 Hz | **6,750 kHz**, 19 000,000 Hz |
| RDS-Hub, 57-kHz-Phase | 2,0 kHz, 0° ±10° | **2,001 kHz, 0,0°** (38-kHz-Rest 0,000 kHz) |
| redsea | PI/PS/RT/PTY/TP/TA/MS/DI/AF/ECC/CT | alle Felder = Soll, 222 Gruppen in 19,7 s (11,3/s), **BLER 0,03 %** |
| `check fm_rds_voll` (RT+, EON, CT, PTYN, LPS, AF-B, 1A LIC/PIN, L/R-Test) | alle Felder | **ok**: RT+ item.artist/item.title, EON 0xD3C5/0xD3C6 (PS, AF, PTY, TA, PIN), CT `02:20:00+02:00`, PTYN, LPS (UTF-8), AF-B tuned/same/regional, Sprache „German“; Kanaltrennung **82,9 / 83,4 dB**, SINAD 80 dB (L/R-Test), Pegel −0,48 dBFS (Soll −0,5) |
| `check fm_mono` | Pilot 0, Hub 75 ±2 % | Pilot 0,000 kHz, **74,34 kHz**, SINAD 97,3 dB |
| `check fm_pegelrampe` (40 % Hub) | 30 kHz | **30,28 kHz**, Pegel −8,28 dBFS (Soll −8,3) |
| `check fm_nachbarkanal` (Störer +200 kHz, −10 dB, beide mit RDS) | Zweitträger solo = Soll; Summensignal informativ | Solo (Nutzträger stumm, Demod bei +200 kHz): PI 0xD3C5, PS „STOERER“, RT, PTY, AF, CT, Pilot 6,749 kHz, RDS 2,000 kHz, **BLER 0,02 %**. Summensignal mit ±150-kHz-ZF: Nutzträger PI/PS/RT/AF/CT weiter dekodiert, Pilot 6,751 kHz, aber BLER 46,6 %, SINAD 46 dB, Spitzenhub 100 kHz – die erwartete Störwirkung eines −10-dB-Nachbarn bei 200 kHz Abstand (Carson ±135 kHz überlappt) |
| Blockgrenzen-Artefakte (Linien im 12,5-Hz-Raster) | keine | stärkste Linie **−148 dBc** (Standard), −214 dBc (Mono); Resampler blockweise = am Stück (max. Differenz 0,0) |
| `live` 60 s, Echtzeit-Leser 1-MB-Blöcke wie `pc/server.py` (`tests/fm_live_test.py`) | 4,096 MS/s, CPU < 50 % | **4 091 601 S/s** im Mittel (min. 3 932 160 in einer Sekunde durch die 1-MB-Granularität), Rückstand max. 70 ms (= ein Block), **CPU 6,4 % im Mittel, max. 14,6 %** eines Kerns (Blockerzeugung allein 15 % laut `tests/fm_profile.py`, davon ×1024/57-Interpolation 0,14 s/4 s, cos/sin 0,16 s/4 s) |
| `ctl` RT/PS/TA zur Laufzeit | Nachweis im 2. Abschnitt | ctl nach 1,07 s bestätigt; Abschnitt B (35–55 s): RT „NEU per ctl …“, PS „CTLTEST“, TA true; Abschnitt A unverändert; CT an der Minutenkante im Live-Betrieb dekodiert |
| Aufräumen | keine Kindprozesse | ffmpeg nach stdout-EOF beendet, Exit 0 |
| `selftest` | ok | DAB-Teil unverändert ok; `fm`: 120/120 Gruppen syndromrein, Zeichensatz (ä 0x91, ö 0x97, ü 0x99, ß 0x8D), MJD, interne Schleife + redsea ok |

Hinweis zum Testleser: mit einem Leser, der 16-kB-Häppchen im 1-ms-Takt holt (wie `win_reader.py --paced`), lag der CPU-Anteil bei 50–57 %,
weil WSL1 je Pipe-Weckruf teuer ist. Der PC-Server liest 1-MB-Blöcke (`pc/server.py`, `rfile.read(min(1<<20, …))`), dafür gilt die Messung oben.

## DVB-T/DVB-C-Transportstrom (`mode: "dvbt" | "dvbc"`, Paket `dvb/`)

Schema, Befehle und Berichtformat: `../SPEC.md`, Abschnitt „DVB-T / DVB-C – Runner-Schema“. `dablab_run.py` gibt jeden Befehl an
`dvb/cli.py` weiter, wenn `mode` `dvbt`/`dvbc` ist (bei `ctl`, wenn `session.json` `standard: dvbt|dvbc` enthält; `media` immer).

```
ffmpeg  (Testbild/Datei/YouTube/Diashow/Video-Playlist, MPEG-2|H.264, MP2|AAC|AC-3, PAT/PMT, -muxrate = Nutzbitrate)
  → tsp  (inject NIT 16 / SDT 17 --replace --poll-files, inject EIT 18 (binär, p/f), inject TDT/TOT 20 + timeref, pcradjust,
          [regulate + -O ip bei UDP])
  → stdout TS | UDP 192.168.2.1:1234 | dvbt_mod/dvbc_mod (-i - -o -) → IQ int16 auf stdout
```

| Datei | Inhalt |
|---|---|
| `dvb/tables.py` | Kanaltabellen (DVB-T E5–E12, K21–K69; DVB-C S02–S41, K05–K11, K21–K69, K02–K04), Nutzbitraten als exakte Brüche |
| `dvb/config.py` | Normalisierung/Prüfung, Übersetzung des Dashboard-Formats (`dvb.programs`, `dvb.si`, `dvbt.fft`), Bitratenbudget, PIDs |
| `dvb/si.py` | NIT/SDT/TDT/TOT als TSDuck-XML, EIT p/f als eigene Binärabschnitte (0 = present, 1 = following, CRC32, Version zählt) |
| `dvb/sources.py` | Quellen, YouTube (yt-dlp-Modul, Fortschritt als JSON), Video-Playlist-Feeder |
| `dvb/chain.py` | Prozesskette, Modulator-Anbindung, Relais auf stdout, Status |
| `dvb/check.py` | tsanalyze, continuity/pcrverify, PCR-Regression (Rate und Jitter), tstables Soll/Ist, ffprobe + Decodertest je Programm |
| `dvb/cli.py` | `live`, `render`, `check`, `validate`, `ctl`, `media` |

Werkzeuge in WSL: ffmpeg 6.1 (`/usr/bin`), TSDuck 3.45-4798 (`tsduck_3.45-4798.ubuntu24_amd64.deb` von GitHub, per
`apt-get install ./tsduck…deb` mit Abhängigkeiten), yt-dlp 2026.10.21 (pip), Modulatoren `/opt/dablab/mod/src/dvb{t,c}_mod`
(x86-Build von `pluto_fw/mod`). Alle Kindprozesse laufen mit `nice 10`, ffmpeg/x264 mit höchstens 4 Threads.

**Quellen**: `testpattern` (ffmpeg testsrc2/smptehdbars/…, Text + Uhrzeit + Laufzeit eingeblendet; Ton Sinus, Sprache per
ffmpeg-flite, Sinus + Sprache, Stille, Rauschen, Datei), `file` (Upload in `media/video/`, Endlosschleife), `youtube` (lädt beim Start,
falls noch nicht vorhanden; vorab: `dablab_run.py media youtube <url> [--max-height 1080]`), `slideshow` (Bilder, Intervall),
`playlist` (Playlist-Dateien wie bei DAB, Einträge sind Videos; Titel für Titel nahtlos in Rohdaten-FIFOs, Bild/Ton werden je Titel
auf gleiche Länge gebracht; EIT present/following aus der Playlist; `ctl playlist next|prev|jump`). Alle Quellen werden auf die
Zielgröße skaliert (Seitenverhältnis erhalten, schwarze Ränder), 25 fps, yuv420p.

**Takt**: Bei `stdout`/`modulator` bremst der Leser (Pluto am PC), es gibt keine Echtzeitbremse im Runner. Bei `udp` regelt
`tsp -P regulate` auf die exakte Nutzbitrate (Systemuhr), der Modulator auf dem Pluto gleicht Restabweichungen mit Nullpaketen aus.

**ctl**: SDT, NIT und EIT liegen als Dateien im Sitzungsordner und werden von `tsp` mit `--poll-files` neu geladen; Name/Anbieter
(SDT-Version +1), LCN (NIT-Version +1), EIT-Titel/-Text (EIT-Version +1, bei Playlists gilt der Text für alle folgenden Titel bis
`"clear": true`), Playlist weiter/zurück/Sprung – alles ohne Neustart der Kette. Die Feeder-Position läuft dem Sendesignal um etwa
2–3 s voraus (Warteschlangen), `now_playing` und EIT wechseln entsprechend früher.

### DVB-Abnahme (WSL1, Ryzen 5 3600, 23.10.2026, `tests/results_wsl/dvb_acceptance.txt`)

| Prüfung | Ergebnis |
|---|---|
| TS-Rate (PCR-Regression über 20 s, alle 7 Konfigurationen) | Abweichung 0 bis −0,4 ppm (Soll ±0,01 % = ±100 ppm); tsanalyze ebenso; z. B. DVB-T 8 MHz 64-QAM 2/3 1/32: 24 128 342,2 b/s |
| PCR-Jitter (Residuen der Regression) | 0 ns (ein Programm), max. 153 ns (DVB-C, 3 Programme) – Soll < 500 ns; pcrverify: 0 PCR > 14 µs |
| Tabellen | PAT, PMT je Dienst, SDT, NIT (Delivery-Deskriptor, service_list, PDS 0x28, LCN 0x83), EIT p/f (2 Abschnitte), TDT, TOT in allen Konfigurationen; Soll/Ist ohne Abweichung |
| Decodertest | alle Programme dekodierbar (150 Bilder in 6 s je TV-Programm, Radio 6 s Ton), 0 Fehlerzeilen; Codecs/Auflösung wie konfiguriert (MPEG-2 SD, H.264 720p/1080p, MP2/AAC/AC-3) |
| Continuity | 0 Fehler (Datei, live 30 s, UDP-Empfang) |
| YouTube | Big Buck Bunny (Blender Foundation, CC BY 3.0), ≤ 480p: 38,8 MB in 3,1–3,2 s, Fortschrittszeilen auf stderr, Index-Eintrag, danach Programm „LAB YouTube“ geprüft |
| UDP an `nc -lu 127.0.0.1:12345` | DVB-C 3 Programme, 60 s eingeschwungen: 50 871 737 b/s bei Soll 50 870 588 b/s (+0,0023 %); 20-s-Fenster: +0,0034 % bzw. +0,0098 % (Messrauschen der Dateigrößenabfrage); Empfang ohne CC-Fehler |
| live stdout mit Echtzeit-Leser 30 s | Leser erhält exakt die Sollrate (24 128 329 b/s bei 24 128 342), max. Verzug 0,35–0,7 s (Anlauf) |
| ungebremster Durchsatz | Testbild SD: 7,75-fache Echtzeit; mit Modulator DVB-T 6 MHz: 4,4-fach (957 Mb/s IQ), DVB-C 3,45 MBd: 2,6-fach |
| Modulatorweg | DVB-T 6 MHz 8k 64-QAM 2/3: 6 s IQ → gr-dtv-Empfänger → TS mit Dienst „LAB HD 1“, NIT, 125 Bilder dekodiert (Fehler nur bis zum ersten IDR). DVB-C 3,45 MBd 256-QAM: numpy-Demodulator MER 37,0 dB, 19 596/19 596 RS-Blöcke fehlerfrei. Pegel live −12,0 dBFS RMS |
| ctl | SDT v1 „LAB TV 1/DABLAB“ → v2 „LAB NEU/DABLAB2“; NIT LCN 1 → 77 (v2); EIT „DAB-Lab Testbild“ → „Neuer Titel per ctl / EIT-Text geändert (Ü)“ (v1); Playlist: EIT folgt den Titeln, ctl-Text erscheint, `next` schaltet |
| Aufräumen | nach live/SIGTERM/stdout-EOF keine Restprozesse |

Tests: `tests/dvb_acceptance.sh` (alles), `dvb_udp_test.sh [config] [s]`, `dvb_ctl_test.sh`, `dvb_mod_test.sh`, `dvb_reader.py`
(Echtzeit-/Durchsatzleser), `dvb_summarize.py` (Kurzfassung eines check-Berichts), `dvb_youtube_bbb.json`.

### DVB-S2 (23.10.)

`mode: "dvbs"` mit `dvbs.system: "dvbs2"` (`modulation` QPSK/8PSK, `pilots`, `roll_off` 0.35/0.25/0.20, `code_rate` je Modulation).
Nutzrate exakt aus `tables.dvbs2_useful_bitrate` (= `dvbs2_ts_bitrate` im Modulator), NIT mit `modulation_system="DVB-S2"`,
`check` vergleicht System, Modulation, FEC und Roll-off. Beispiel `examples/dvbs2_testbild_hd.json` (QPSK 3/4, 22 MBd, Pilots):
`check` ok, 31 945 662 b/s gemessen (Soll 31 945 676), H.264 1080p + AAC decodiert. `output.kind = "modulator"` nutzt lokal
`dvbs2_mod --sps 2` (`rf.sample_rate` = 2 · SR); gesendet wird über den Gerätepfad (`dvbs2_mod --sps 1` auf dem Pluto).

### Amateurfunk-DATV (23.10.)

`dvbs.use: "ham"` sendet DVB-S/S2 direkt auf der Amateurfunkfrequenz (`rf.channel: "ham"`, `rf.freq_hz` = Signalmitte) statt auf der
Sat-ZF. Pflicht: `dvbs.callsign` (Rufzeichen) im Dienst- oder Anbieternamen; das Signal (SR · (1 + α)) muss ganz im Band liegen
(`tables.HAM_BANDS`: QO-100-Breitband-Uplink 2401,5–2409,5 MHz, 13/23/70/6 cm; ein Signal, das QO-100 nur teilweise trifft, wird
abgelehnt). Symbolrate 25 kS/s … 3,5 MS/s; IQ per USB mit 2/4/8 Samples/Symbol (`tables.ham_sps`, ≥ 600 kS/s am DAC), lokal
`dvbs2_mod`/`dvbs_mod --sps N`. NIT: bei QO-100 die Downlink-Frequenz (Uplink + 8089,5 MHz). Unter 2 Mbit/s Nutzrate werden PAT/PMT
alle 0,4 s statt je Bild gesendet, EIT/TDT mit 3/1 kbit/s, SI-Reserve ~8 % (sonst fräße die SI ein Drittel einer 333-kS/s-Sendung).
Beispiel `examples/datv_qo100.json` (QPSK 2/3, 333 kS/s, H.264 640×360): Ende-zu-Ende ohne HF geprüft
(`pluto_fw/mod/tests/datv_e2e.sh`: Runner → dvbs2_mod → `dvbs2_hardrx.py` → 121 Rahmen, 0 CRC-Fehler, Bild und Ton dekodiert).

### DVB: Grenzen und offene Punkte

- Kein HF-Test: MER/BER und Receiver-Suchlauf (Name, LCN, EIT am echten Fernseher/Stick) sind nicht geprüft. Der Pluto und der Server
  wurden nicht angefasst.
- 7/8-MHz-DVB-T und 6,9-MBd-DVB-C passen nicht durch USB; dafür gibt es den UDP-Weg zum Modulator auf dem Pluto (Firmware entsteht parallel).
  Der UDP-Strom ist auf Systemuhr geregelt; der Modulator muss mit Nullpaketen ausgleichen (≈ 70–95 % Nullanteil in den Beispielen, reichlich).
- HD wird progressiv kodiert (1080p25); interlaced nur bei MPEG-2 (`video.interlaced`). Teletext und DVB-Untertitel sind nicht umgesetzt.
- Bei einer Playlist läuft `now_playing`/EIT dem Bild um etwa 2–3 s voraus. Sehr kurze Titel (< 3 s) sind möglich, die EIT ändert sich dann sehr oft.
- DVB-C-VHF-Kanäle (S02–S20, K05–K11) im 8-MHz-Raster sind eine Annahme; Netze mit 1-MHz-Versatz per `custom`-Frequenz einstellen.
- `lock` im check-Bericht ist immer `true` (reine TS-Prüfung).

### DVB-Nachtrag: nahtlose Schleife und HD-Transkodierung (23.10.)

- `source.loop_mode: "feeder"` (Standard für geschleifte Dateien/YouTube): Dekodieren Durchlauf für Durchlauf in die Rohdaten-FIFOs
  des Mux-ffmpeg, Ton je Durchlauf exakt auf Bildlänge (`apad` + `-t` Videodauer). `"stream_loop"` = alter Weg.
- Nachweis `tests/dvb_loop_bbb_test.sh` (Big Buck Bunny 634,6 s, 1300 s gerendert = zwei Schleifenstellen, `results_wsl/dvb_loop_bbb.txt`):
  feeder: tsp continuity 0 Fehler, pcrverify 46 046 PCR ohne Jitter > 1 ms, PCR-Rate +0,000 ppm, Jitter 0 ns; Video-DTS 32 500 Schritte
  à 3600 ohne Sprung, Audio 54 167 Schritte à 2160 ohne Sprung. An den Schleifenstellen nur der Inhalt der Datei selbst (Schwarz 0,40 s
  = 0,32 s Ende + 0,08 s Anfang der Quelle). stream_loop zum Vergleich: zusätzlich ein Standbild von 9 Bildern (0,36 s) je Schleifenstelle.
- Ungleich lange Spuren (`tests/dvb_loop_av_test.sh`): Ton 0,7 s länger → feeder 0 doppelte Bilder; Ton 0,6 s kürzer → feeder füllt 0,6 s
  Stille ein (Bild/Ton bleiben synchron), stream_loop setzt den Ton zu früh fort (Versatz wächst je Durchlauf).
- In keinem Fall sprangen PTS/DTS/PCR im erzeugten TS (die Kette transkodiert immer; der Muxer zählt selbst). Der sichtbare
  „Aussetzer an der Schleifenstelle“ ist das Standbild bzw. der A/V-Versatz von `-stream_loop`.
- HD live: H.264 1080p25 `veryfast` mit 4 Kernen (taskset 0–3) aus 1080p60-Quelle: 2,46× Echtzeit (faster 1,97×, superfast 4,55×,
  nur Dekodieren 6,3×). Vorab-Transkodieren nicht nötig, deshalb nicht gebaut.
- YouTube verlangte nach mehreren Downloads am selben Tag eine Anmeldung („Sign in to confirm you're not a bot“).
  Abhilfe: Cookie-Datei (yt-dlp `--cookies`) – im Runner noch nicht verdrahtet.

## Analog-TV PAL (`mode: "pal"`, Paket `pal/`)

Schema und Befehle: `../SPEC.md`, Abschnitt „Analog-TV PAL“. Dateien: `pal/systems.py` (Normen B/G, D/K, I; Kanaltabellen mit
Bildträgern), `pal/gen.py` (Schleife: Verfahren aus `pluto_fw/mod/tools/pal_bg_gen.py`, bitgleich übernommen und um Normen, Bild- und
Tonquelle erweitert; Strom-Encoder für live), `pal/demod.py` (Prüfdemodulator nach `pal_check.py`, erweitert um FM-Ton, Farbträger,
Sollbild-Vergleich), `pal/cli.py`. Beispiele: `examples/pal_k36_testbild.json` (Schleife wie am LG-TV), `pal_live_testbild.json`,
`pal_live_video.json`.

Der LO des Pluto liegt **nicht** auf `rf.freq_hz` (Bildträger), sondern auf `rf.lo_hz` (Schleife) bzw. `rf.lo_hz_live` (live);
beides gibt `validate`/`render`/`live` aus.

### PAL-Abnahme (WSL1, 23.10.2026, `tests/pal_test.sh` → `results_wsl/pal_acceptance.txt`)

| Prüfung | Ergebnis |
|---|---|
| Übernahme | gleiches Bild wie das Original → 1 280 000 Samples zu 100 % identisch (max. Abweichung 0 LSB) |
| render K36 | 5,12 MB int16, 160 ms, LO 593,5 MHz; Spektrum: Ton −9,0 dB / Farbträger −17,8 dB zum Bildträger, Restseitenband −1 MHz −40 dB, RMS −6,0 dBFS, Spitze −0,9 dBFS, 8,4 s Rechenzeit |
| check (FFT und Strom-Encoder) | Zeilen 15 625 Hz, Halbbild-Synchronisation alle 312,5 Zeilen, Austast 0,719/0,716 (Soll 0,73), Weiß 0,085 (Soll 0,10), Ton/Bild −13,3 dB (Soll −13), Ton 1000,1 Hz, Hub 52,4 kHz (50 kHz + Preemphase), Bildkorrelation 0,963, Schleifenstelle unauffällig, kein Clipping |
| Normen D/K, I | check ohne Abweichung (Versatz 2,675 bzw. 2,175 MHz) |
| live Durchsatz (7 MS/s, ungebremst) | 1 Prozess 0,72–0,80×, 2 Prozesse 1,24×, 3 Prozesse 1,61–1,79× Echtzeit; danach keine Restprozesse |
| live demoduliert | Testbild mit Uhr: Zeilen 15 625 Hz, Ton 1000 Hz, Hub 52,3 kHz, Ton/Bild −13,25 dB; Video (Big Buck Bunny mit Originalton): Bild erkennbar, Ton/Bild −14,3 dB |

### PAL: Grenzen

- Live braucht 7 MS/s über USB = 98 % der gemessenen 7,15 MS/s; am Gerät nicht geprüft. 8 MS/s gibt es nur als Schleife (zyklischer Puffer).
- B/G bei 7 MS/s: Tonträger bei +3,4 MHz, also 0,1 MHz unter fs/2 – eng, aber in der Demodulation sauber. D/K/I nur in der Schleife geprüft.
- Kein Zweikanalton (A2) und kein NICAM, kein Videotext. Halbbilder eines progressiven Videos zeigen denselben Zeitpunkt.
- Die Hüllkurven-Prüfung bewertet nur die Helligkeit bis ±1 MHz; die Farbe wird nur über den Farbträgerpegel geprüft, nicht dekodiert.

## GPU-Encoding (NVENC), DVB-S, Windows-Quellen (`dvb/nvenc.py`)

Schema: `../SPEC.md`, Abschnitt „GPU-Encoding (NVENC), DVB-S …“. `video.encoder: "nvenc"` startet je Dienst ein Windows-ffmpeg
(`/mnt/c/ffmpeg/bin/ffmpeg.exe`, gyan 2026-03 essentials) direkt aus WSL1; dessen roher H.264-/HEVC-Strom geht per Pipe in den Mux-ffmpeg
(`-c copy`), SI/PCR/Stopfen/Regelung wie bisher. Ton: aus derselben Datei (WSL), Testton oder dshow-Gerät (zweites Windows-ffmpeg, PCM).

### Messungen (23.10.2026, GTX 1080, Treiber 580.88, Ryzen 5 3600, WSL1)

| Prüfung | Ergebnis |
|---|---|
| UHD: BBB 2160p30 → HEVC Main 2160p25 16 Mbit/s, DVB-S 22 MBd 7/8 | check ohne Abweichung: Rate 35 480 388,9 b/s (Soll 35 480 392,2, −0,1 ppm), PCR-Jitter 40 ns, alle Tabellen inkl. NIT satellite_delivery (11,038 GHz, 19,2° O, V, 22 MBd, 7/8), Dekodertest 150 Bilder 3840×2160 |
| Durchsatz live (stdout, ungebremst) | UHD allein 2,2–2,3× Echtzeit; drei Dienste (HD H.264 + UHD + UHD HDR10, drei NVENC-Sitzungen) 1,35× |
| HDR10 aus HDR-Quelle (Sol Levante, SID 3) | Ausgabe Main 10, yuv420p10le, BT.2020/PQ/bt2020nc; `ffprobe -show_frames`: Mastering display (R x 34000/50000, L 1000/0,005 cd/m²) und Content light level (MaxCLL 1000, MaxFALL 400) in jedem Schlüsselbild – NVENC reicht die SEI durch |
| HDR10-Kennung einer SDR-Quelle | Runner setzt die SEI selbst ein (`inject_sei: auto`) → ffprobe zeigt dieselben Mastering-/CLL-Daten |
| HLG | Main 10, transfer arib-std-b67, primaries bt2020 |
| Schleife (NVENC, Ton 0,7 s länger als Bild, 3 Schleifenstellen) | 0 doppelte Bilder, kein Schwarz, keine Tonlücke; DTS 500 × 3600, Audio 938 × 1920 ohne Sprung; pcrverify ohne Jitter |
| Demo-Probelauf an 127.0.0.1 (UDP, eigener Empfänger, 60 s) | 35 480 179 b/s (−0,0006 %), 7 TS-Pakete je Datagramm, pcrverify ohne Fehler, Dienst „DABLAB Sat UHD“ 0x1F, HEVC 3840×2160 15,2 Mbit/s |
| gdigrab (Bildschirm) → h264_nvenc 1080p | check ohne Abweichung |
| dshow „OBS Virtual Camera“ | Gerät gelistet, liefert aber kein Bild („Could not find output pin“ – die virtuelle Kamera ist in OBS nicht gestartet) |
| ddagrab | liefert in dieser Sitzung keine Bilder (auch direkt im Windows-ffmpeg nicht); Wachhund bricht nach 30 s sauber ab – gdigrab verwenden |
| Aufräumen | nach jedem Lauf kein ffmpeg.exe mehr in der Taskliste |

### Live-Demo DVB-S UHD (für den Modulator-Agenten; selbst NICHT gesendet)

Skript `demo/sat_uhd_nvenc.sh`, Konfigurationen `examples/dvbs_uhd_nvenc.json` (nur SID 2 „DABLAB Sat UHD“, BBB 2160p komplett, endlos,
HEVC 2160p25 16 Mbit/s, ONID 0xFF03, TSID 3, NIT FEC 7/8) und `examples/dvbs_sat3_nvenc.json` (ganzer Transponder live: SID 1 HD H.264 7 Mbit/s aus
`bbb_sat.ts`, SID 2 UHD 14 Mbit/s, SID 3 HDR10 Main10 9,5 Mbit/s aus `hdr_hevc.ts`).

```
# 1. bisherigen Strom beenden (nur ein Sender auf 192.168.2.1:1234):
wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/pluto_fw/mod/tests/k56/stream_sat_3.sh stop
# 2. optional Probelauf ohne Pluto (UDP an 127.0.0.1, misst die Rate):
wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/runner/demo/sat_uhd_nvenc.sh dry 60
# 3. umschalten (dvbs_mod --rate 7/8 auf dem Pluto laeuft weiter und liest udp://:1234):
wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/runner/demo/sat_uhd_nvenc.sh start            # nur SID 2
wsl -d dablab -u root -- bash /mnt/<drive>/.../dablab/runner/demo/sat_uhd_nvenc.sh start3           # alle drei Dienste
# Status / EIT aendern / Stopp:
… sat_uhd_nvenc.sh status
… sat_uhd_nvenc.sh ctl '{"eit":{"service":"uhd","title":"Big Buck Bunny UHD","text":"live NVENC"}}'
… sat_uhd_nvenc.sh stop
```
Ein anderes Ziel: `start 192.168.2.1:1234` bzw. `start <host:port> <config>`. Der Strom ist auf 35 480 392,2 b/s geregelt (tsp regulate),
7 TS-Pakete je UDP-Datagramm; der Pluto-Modulator gleicht die Uhrabweichung mit Nullpaketen aus (61 % Nullanteil bei nur SID 2).

### NVENC: Grenzen

- WSL1 sieht die GPU nicht: der Encoder läuft immer als Windows-Prozess. Seine Priorität lässt sich aus WSL nicht senken (nice wirkt nur auf
  den WSL-Stellvertreter); die CPU-Last (Format-Umwandlung 4K, p010) ist mit `-threads 4 -filter_threads 2` begrenzt.
- Pascal: HEVC ohne B-Frames, kein AV1-Dekoder; die Zahl gleichzeitiger NVENC-Sitzungen begrenzt der Treiber (3 liefen problemlos).
- Ton aus dshow wird nicht mit dem Bild synchronisiert (zwei getrennte Echtzeit-Prozesse); Bildschirm-/Kameraquellen ohne Ton nutzen Testton.
