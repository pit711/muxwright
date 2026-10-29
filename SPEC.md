# DAB-Lab – gemeinsame Spezifikation (verbindlich für alle Teile)

Ziel: Labor-DAB/DAB+-Sender mit dem ADALM-Pluto zur Entwicklung und Zertifizierungsvorbereitung eines DAB+-Radios.
Eine Weboberfläche steuert alles, was ETSI EN 300 401 / TS 102 563 hergeben und die ODR-mmbTools umsetzen können.

⚠️ **Senden nur abgeschirmt** (Kammer, Kabel mit Dämpfung, Dummy-Load). Der Pluto startet immer mit −89,75 dB
und `enabled=false`. Kein Code darf je eine Dämpfung über 0 dB oder unter −89,75 dB setzen.

## Architektur

```
Browser ──HTTP──► PC: pc/server.py (:8792, Windows, Python 3.13, numpy/scipy, pylibiio)
                     │  ssh user@mac.local (DABLAB_SSH_HOST, Schlüssel, BatchMode)
                     ▼
                 Mac: ~/dablab/runtime/dablab_run.py (macOS arm64, /usr/bin/python3 oder Homebrew-Python)
                     ffmpeg → odr-audioenc (+ odr-padenc) → FIFO → odr-dabmux → odr-dabmod → stdout (IQ)
                     │
PC ◄── IQ-Strom (ssh stdout) ── server.py ──libiio usb:──► Pluto (TX)   + RX-Mitschnitt für Kontrollempfang
```

- Werkzeuge auf dem Mac: `~/dablab/prefix/bin/{odr-audioenc,odr-padenc,odr-dabmux,odr-dabmod,welle-cli}`,
  dazu `/opt/homebrew/bin/{ffmpeg,magick}`. Quellen in `~/dablab/src/*`. Beispiele: `~/dablab/src/ODR-DabMux/doc/*.mux`,
  `~/dablab/src/ODR-DabMod/doc/example.ini`. Rauchtest: `~/dablab/smoke/smoke.sh`.
- **macOS-Fallen**: BSD-getopt, deshalb Optionen **vor** die Eingabedatei (`odr-dabmod -f out -F s16 -m 1 in.eti`).
  `sed -i ""`. In zsh über ssh kein `==`.
- Der Pluto am PC: `usb:` (libiio-DLL in `E:\espsdr\pluto\libiio-win`, pylibiio braucht `os.add_dll_directory` + gepatchtes
  `ctypes.util.find_library`, siehe `pc/iio_load.py` unten) oder `ip:192.168.2.1`. Gemessen: TX-Streaming per USB max. 7,15 MS/s,
  per IP 6,29 MS/s. **2,048 MS/s lehnt der AD9361-Treiber ohne FIR ab** → Ausgaberate **4,096 MS/s** (odr-dabmod `-r 4096000`).
  Firmware v0.31, AD9364-Hack aktiv (70 MHz–6 GHz).
- Takt: Die ganze Kette wird vom Pluto gebremst (blockierendes Schreiben, keine Echtzeit-Schrittmacher auf dem Mac).
  ffmpeg liefert PCM per Pipe, odr-audioenc schreibt `.dabp` in eine FIFO, odr-dabmux liest die FIFO blockierend.
  Ergebnis: keine Drift, keine Schleifen, kein Puffer-Überlauf.

## Mac-Runner-Protokoll (`dablab_run.py`)

Alle Befehle lesen die Konfiguration (JSON, Schema unten) aus `--config <datei>`. Medien liegen in `~/dablab/runtime/media/`.

| Befehl | Wirkung | Ausgabe |
|---|---|---|
| `live --config c.json` | Startet die Kette. Läuft, bis stdout geschlossen wird oder SIGTERM kommt; danach alle Kindprozesse aufräumen | **stdout**: IQ int16 LE interleaved (I,Q) mit `rf.sample_rate`; **stderr**: JSON-Zeilen `{"t":..,"lvl":"info|warn|error","msg":..}` |
| `render --config c.json --seconds N --out f --rate 2048000 --format cf32\|s16` | Rendert N s in eine Datei (für Prüfung/Schleifenmodus) | JSON-Zusammenfassung |
| `check --config c.json --seconds 20` | render 2,048 MS/s cf32 + welle-cli `-w` → liest `/mux.json` (+ Slides) | JSON-Bericht (s. u.) |
| `decode --iq f.cf32.iq [--seconds 20]` | dekodiert einen Mitschnitt (RX-Kontrolle) | JSON-Bericht |
| `ctl --session <id> '<json>'` | Live-Änderungen ohne Neustart: `{"dls":{"service":"srv1","text":"..."}}`, `{"slide":{"service":"srv1","file":"media/x.jpg"}}`, `{"announcement":{"cluster":1,"active":true}}`, `{"label":{"service":"srv1","label":"..","shortlabel":".."}}` (DabMux-Fernsteuerung über telnet 127.0.0.1:12721 bzw. zmq) | JSON `{"ok":true}` |
| `selftest` | prüft Werkzeuge und Versionen | JSON |

Live-Sitzungen legen ihren Zustand in `~/dablab/runtime/sessions/<id>/` ab (DLS-Dateien, Slide-Ordner, Logs). `live` gibt als
erste stderr-Zeile `{"session":"<id>"}` aus.

**Prüfbericht** (check/decode):
```json
{"ok":true,"sync":true,"snr_db":..,"freq_corr_hz":..,"ensemble":{"eid":"0x4fff","label":"DABLAB","shortlabel":"..","ecc":"0xe0","lto":..,"datetime":".."},
 "services":[{"sid":"0xd001","label":"..","shortlabel":"..","pty":..,"language":..,"codec":"HE-AACv2","bitrate":88,"protection":"EEP 3-A",
   "samplerate":48000,"channels":2,"dls":"..","dlplus":[..],"slides":[{"file":"..","w":..,"h":..}],"audio_level_db":[..,..],"errors":{"frame":..,"rs":..,"aac":..}}],
 "tii":[..],"announcements":[..],"raw":{ "mux.json": ... }}
```

## Konfigurationsschema (`config.json`)

```jsonc
{
  "name": "Standard",                       // Name des Presets
  "mode": 1,                                // Übertragungsmodus I–IV (Band III: I; L-Band: II/IV)
  "ensemble": {
    "eid": "0x4FFF", "ecc": "0xE0",         // Deutschland: ECC E0, Länderkennung im EId (0x1… usw.)
    "label": "DABLAB", "shortlabel": "DABLAB",   // 16 Zeichen, shortlabel = Auswahl daraus (max. 8)
    "charset": "ebu" | "utf8",              // Kennzeichnung FIG 1/FIG 2
    "international_table": 1,
    "lto": "auto" | -24..24,                // Ortszeitversatz in halben Stunden, FIG 0/9
    "datetime": true,                       // FIG 0/10
    "alarm_flag": false,                    // AL-Flag FIG 0/0 (Alarmdurchsagen, Cluster 0xFF)
    "reconfig_counter": "hash" | "off" | 0..1023
  },
  "services": [{
    "key": "srv1",                          // interner Schlüssel [a-z0-9_]
    "sid": "0xD001",                        // 16-bit (Audio) oder 32-bit (Daten)
    "ecc": null,                            // abweichende ECC, sonst Ensemble
    "label": "Testton 1 kHz", "shortlabel": "Ton1k",
    "pty": 0, "pty_dynamic": false,         // Programmart 0–31 (RDS/DAB-Tabelle)
    "language": "0x08",                     // FIG 0/5 (0x08 = Deutsch)
    "type": "dabplus" | "dab" | "data",
    "audio": {
      "source": { "kind": "file" | "tone" | "silence" | "noise" | "pinknoise" | "sweep" | "lr_test" | "polarity",
                  "file": "media/song.mp3", "freq_hz": 1000, "level_dbfs": -18, "left_hz": 1000, "right_hz": 400,
                  "sweep_from_hz": 20, "sweep_to_hz": 20000, "sweep_s": 10, "loop": true },
      "codec": "he-aac-v2" | "he-aac" | "aac-lc" | "mp2",   // mp2 nur bei type "dab"
      "samplerate": 48000 | 32000 | 24000,  // DAB+: 48/32 (mit SBR intern 24/16); MP2: 48/24
      "channels": 2 | 1,
      "bitrate": 88,                        // DAB+: 8..192 (Vielfaches von 8); MP2: 32..384
      "mp2_mode": "s" | "j" | "d" | "m",
      "gain_db": 0, "bandwidth_hz": 0       // 0 = Encoder-Standard
    },
    "protection": { "profile": "EEP_A" | "EEP_B" | "UEP", "level": 3 },   // EEP 1–4, UEP 1–5 (nur MP2)
    "pad": {
      "enable": true, "bytes": 58,          // X-PAD-Länge (DAB+: 0..255 typisch 16..58)
      "dls": { "enable": true, "text": "Hallo DAB+", "dlplus": { "enable": false, "artist": "", "title": "", "album": "", "station": "", "program": "", "url": "" },
               "rotate": ["Text 1", "Text 2"], "rotate_s": 10, "charset": "utf8" | "ebu", "remove_label": false },
      "sls": { "enable": true, "slides": ["media/slide1.jpg"], "interval_s": 10, "max_bytes": 50000,
               "raw": false, "categories": false }    // MOT Slideshow (X-PAD), Auflösung 320×240 empfohlen
    },
    "data": {                               // nur bei type "data" (Paketmodus)
      "kind": "spi" | "slideshow_packet" | "tpeg" | "journaline" | "raw",
      "bitrate": 16, "packet_address": 1, "files": ["media/..."]
    },
    "spi": { "logo": "media/logo.png" },    // Senderlogo/Thumbnails (SPI, ETSI TS 102 818/TS 102 371) – falls umsetzbar
    "announcements": { "supported": ["Alarm","Traffic","Travel","Warning","News","Weather","Event","Special","Rad_info","Sports","Finance"],
                       "clusters": [1] },   // FIG 0/18 ASu
    "linking": { "fm_pi": null, "fm_freqs_mhz": [], "other_ensembles": [] }  // FIG 0/6, 0/21, 0/24
  }],
  "clusters": [ { "id": 1, "flags": ["Traffic"], "service": "srv1", "active": false } ],  // FIG 0/19 ASw; id 0xFF = Alarm
  "tii": { "enable": false, "comb": 1, "pattern": 11, "old_variant": false },
  "rf": {
    "channel": "6C", "freq_hz": 185360000,  // Kanaltabelle unten, oder freie Frequenz (channel = "custom")
    "offset_hz": 0,                         // Frequenzversatz für Empfänger-Toleranztests
    "lo_offset_hz": 0,                      // digitale Verschiebung gegen LO-Durchgriff (Signal bleibt auf freq_hz)
    "atten_db": 60,                         // Pluto-Dämpfung 0…89,75 dB (hardwaregain = −atten)
    "sample_rate": 4096000,
    "backoff_db": 12,                       // Abstand RMS zu Vollaussteuerung (Crest-Faktor OFDM)
    "cfr": false,                           // Crest-Factor-Reduction in odr-dabmod (falls verfügbar)
    "enabled": false,                       // false = Strom läuft, Pluto stumm (−89,75 dB)
    "cal_dbm_at_0db": {}                    // optional: gemessene Ausgangsleistung je Kanal bei 0 dB Dämpfung → Pegelanzeige in dBm
  }
}
```

Kapazität: 864 CU pro Ensemble. EEP-A (Bitrate 8n): CU = 12n / 8n / 6n / 4n für 1-A…4-A. EEP-B (Bitrate 32n): 27n / 21n / 18n / 15n.
UEP: Tabelle aus EN 300 401 Tab. 6 (auch in `ODR-DabMux/src/MuxElements.cpp`).

## Kanaltabelle (MHz)

Band III: 5A 174.928, 5B 176.640, 5C 178.352, 5D 180.064, 6A 181.936, 6B 183.648, **6C 185.360**, 6D 187.072, 7A 188.928,
7B 190.640, 7C 192.352, 7D 194.064, 8A 195.936, 8B 197.648, 8C 199.360, 8D 201.072, 9A 202.928, 9B 204.640, 9C 206.352,
9D 208.064, 10A 209.936, 10N 210.096, 10B 211.648, 10C 213.360, 10D 215.072, 11A 216.928, 11N 217.088, 11B 218.640,
11C 220.352, 11D 222.064, 12A 223.936, 12N 224.096, 12B 225.648, 12C 227.360, 12D 229.072, 13A 230.784, 13B 232.496,
13C 234.208, 13D 235.776, 13E 237.488, 13F 239.200.
L-Band: LA 1452.960 … LW 1490.624 in 1,712-MHz-Schritten (LA, LB, … LP).

## HTTP-API (PC-Server, alles JSON)

| Methode | Pfad | Inhalt |
|---|---|---|
| GET | `/` | Dashboard (`web/index.html` + Assets aus `web/`) |
| GET | `/api/state` | `{"config":{..},"tx":{"state":"idle|starting|running|error","since":..,"session":..,"underruns":..,"samples_sent":..,"rate_ms":..,"buffer_ms":..},"pluto":{"connected":..,"uri":..,"fw":..,"temp_c":..,"lo_hz":..,"atten_db":..},"mac":{"reachable":..,"tools":..},"log":[..letzte 200 Zeilen..],"check":{letzter Prüfbericht},"monitor":{letzter RX-Bericht}}` |
| PUT | `/api/config` | ganze Konfiguration setzen (validiert; Fehler 400 mit Liste). Wenn TX läuft und sich Struktur ändert → `{"restart_required":true}` |
| POST | `/api/tx/start` | Kette starten (Pluto bleibt stumm, bis `rf.enabled`) |
| POST | `/api/tx/stop` | Kette stoppen, Pluto −89,75 dB |
| POST | `/api/rf` | Teilupdate RF ohne Neustart: `{"channel","freq_hz","offset_hz","atten_db","enabled"}` |
| POST | `/api/live` | Live-Änderung → Mac `ctl` (DLS, Slide, Durchsage an/aus, Label) |
| POST | `/api/upload?kind=audio|slide|logo&name=..` | Rohdaten im Body → `media/` lokal + Mac `~/dablab/runtime/media/` |
| GET | `/api/media` | Liste der Medien |
| POST | `/api/check` | Offline-Prüfung der aktuellen Konfiguration auf dem Mac (welle-cli) → Prüfbericht |
| POST | `/api/monitor` | `{"seconds":8,"rx_gain_db":30}` RX-Mitschnitt am Pluto auf der Sendefrequenz → 2,048 MS/s cf32 → Mac `decode` → Bericht |
| GET/PUT/DELETE | `/api/presets[/name]` | Presets in `presets/*.json` |
| POST | `/api/sweep` | Pegelrampe für Empfindlichkeitsmessung `{"from_db":40,"to_db":89,"step_db":1,"dwell_s":5}` (Abbruch per `/api/sweep/stop`) |
| GET | `/api/events` | Server-Sent Events: Zustand/Log alle 500 ms |

## Ordner

```
dablab/
  SPEC.md            diese Datei
  runner/            dablab_run.py (läuft in WSL „dablab“, /opt/dablab/bin; Mac nur noch Ausweichweg per ssh)
  wsl/               WSL1-Distribution „dablab“ (fs/), build_tools.sh, fixdns.sh
  launcher/          Quelle der DABLab.exe
  pc/                server.py, pluto.py, iio_load.py, presets/                     ← Agent „pc“
  web/               index.html, app.js, style.css (keine CDN-Pflicht, offline lauffähig)  ← Agent „web“
  test/              E2E-Tests (Playwright Python, Chromium headless ist installiert)
  media/             Beispielmedien
```

## Ergänzung 23.10.: Playlist als Audioquelle

`audio.source.kind = "playlist"` mit
```jsonc
"source": { "kind": "playlist", "playlist": "name",          // Name der Playlist in runtime/playlists/<name>.json
            "shuffle": false, "loop": true, "crossfade_s": 0, "normalize": true,   // Lautheitsangleich (ffmpeg loudnorm, EBU R128 −23 LUFS)
            "dls_from_tags": true }                          // Titel/Interpret aus den Tags → DLS + DL Plus (ITEM.TITLE/ARTIST), Umschalten bei Titelwechsel
```
- Playlist = JSON `{"name":..,"items":[{"file":"media/x.mp3","title":..,"artist":..,"duration_s":..}]}`; Import von `.m3u/.m3u8/.pls` (relative Pfade zu `media/`), Export als `.m3u8`.
- Formate: alles, was ffmpeg liest: mp3, ogg/vorbis, opus, flac, wav, aac/m4a, wma. Upload über `/api/upload?kind=audio`.
- Runner: ffmpeg concat-Demuxer oder Titel für Titel in dieselbe PCM-Pipe (nahtlos, Sample-genau 48 kHz); bei Titelwechsel DLS/DL Plus über die bestehende ctl-Schnittstelle setzen; `live`-Status meldet `now_playing`.
- API: `GET/PUT/DELETE /api/playlists[/name]`, `POST /api/playlists/<name>/import` (m3u-Text im Body), `GET /api/playlists/<name>/export`; `POST /api/live {"playlist":{"service":"srv1","action":"next|prev|jump","index":n}}`.
- Zustand: `tx.now_playing = {service, index, title, artist, elapsed_s, duration_s}` je Dienst.
- Dashboard: Bereich „Dienste → Quelle → Playlist“ mit Liste (Drag-Sortierung, hinzufügen per Upload/Medienbibliothek, entfernen), Jetzt-läuft-Anzeige mit Fortschritt, Weiter/Zurück, Zufall/Endlos, M3U-Import/Export; Medienbibliothek als eigener Bereich.

## Ergänzung 23.10.: weitere Standards (DVB-T, DVB-C, UKW) – Vorgaben

- Das Dashboard bekommt einen **Standard-Umschalter** (`mode`: `dab` | `fm` | `dvbt` | `dvbc`), je Standard ein eigener Bereich mit den
  normgerechten Parametern; HF-Bereich, Pegelrampe, Kontrollempfang, Presets/Testpläne/Protokoll und Log gelten für alle.
- **Videoquelle für DVB-T/DVB-C**: Datei (Upload), Testbild (ffmpeg testsrc/smptebars + Ton), **YouTube-Video** (per `yt-dlp` in
  `runtime/media/video/` laden, Fortschritt im Dashboard, dann wie Datei; Auflösung/Codec vor dem Muxen wählbar: MPEG-2 oder H.264,
  SD/HD, Audio MP2/AAC/AC-3), Playlist aus Videos. Mehrere Programme je Transportstrom (Service-Name, LCN, Provider).
- Transportstrom mit vollständigen SI-Tabellen (PAT/PMT/SDT/NIT/EIT/TDT) und konstanter Bitrate (Nullpakete) – TSDuck bevorzugt.
- Die Modulation läuft, wo es die Abtastrate erzwingt, **auf dem Pluto** (eigene Firmware, Ordner `pluto_fw/`); der PC/WSL liefert dann
  den Transportstrom per UDP an 192.168.2.1 statt IQ per USB. Die Firmware muss weiterhin iiod/libiio für DAB/UKW-Streaming bieten.
- Sicherung der Werksfirmware: `pluto_fw/backup/mtd0..3_*.bin` (23.10.2026), Rettung per DFU (`dfu_sf` in U-Boot) dokumentieren.

## Ergänzung 23.10.: UKW (`mode: "fm"`) – FM-Stereo-MPX mit RDS

Der Runner erzeugt bei `mode: "fm"` selbst das IQ-Signal (Paket `runner/fm/`, numpy/scipy, kein ODR). Runner-Protokoll
(`live`/`render`/`check`/`decode`/`ctl`/`selftest`/`validate`) und IQ-Format (int16 I/Q, 4,096 MS/s auf stdout, Status-JSON auf
stderr, erste Zeile `{"session":..}`) sind identisch zu DAB; der PC-Server braucht nur `rf.freq_hz` (aus `fm.freq_mhz`).
Kanalraster 87,5–108,0 MHz in 100-kHz-Schritten (Warnung außerhalb des Rasters, Fehler außerhalb des Bandes).

```jsonc
{
  "name": "UKW Standard", "mode": "fm",
  "fm": {
    "freq_mhz": 100.0,                       // 87,5..108,0 -> rf.freq_hz
    "deviation_khz": 75,                     // Gesamthub (Spitze); Audiohub = deviation - pilot - rds.level
    "pilot_khz": 6.75,                       // Pilotton 19 kHz, Hub (Nenn 6,75 = 9 %), 0 = kein Pilot
    "preemphasis_us": 50,                    // 50 | 75 | 0 (aus)
    "stereo": true,                          // false = Mono (kein Pilot, kein 38-kHz-Anteil)
    "limiter": { "enable": true, "threshold": 1.0, "release_db_s": 3, "clip": true },  // Spitzenbegrenzer nach Preemphasis
    "mpx_power_dbr": null,                   // z. B. 0 -> MPX-Leistung nach BS.412 (0 dBr = Sinus mit 19 kHz Hub) begrenzen
    "audio_gain_db": 0,
    "audio": { "source": { "kind": "tone|file|playlist|silence|noise|pinknoise|sweep|lr_test|polarity", ... } }, // wie DAB (48 kHz Stereo)
    "rds": {
      "enable": true, "pi": "0xD3C4", "ps": "DABLAB", "rt": "max. 64 Zeichen",
      "rtplus": [ {"type": "ITEM.ARTIST", "text": "..."} , {"type": 1, "start": 0, "len": 10} ],   // max. 2 Tags je 11A
      "pty": 10, "tp": true, "ta": false, "ms": true,
      "di": { "stereo": true, "artificial_head": false, "compressed": false, "dynamic_pty": false },   // oder Zahl 0..15
      "af": [100.0, 101.3], "af_method": "A" | "B", "af_regional": [89.0],   // Methode B: Paare mit der eigenen Frequenz
      "ecc": "0xE0", "lic": null, "pin": "DD:HH:MM",                          // Gruppe 1A
      "ct": true, "utc_offset_halfhours": "auto", "ct_latency_s": 0,          // 4A an der Minutenkante (+-0,1 s, Latenzausgleich)
      "ptyn": "", "lps": "UTF-8, 32 Byte",                                     // 10A, 15A
      "eon": [ {"pi": "0xD3C5", "ps": "ANDERER", "pty": 3, "tp": true, "ta": false, "af": [99.9],
                "mapped_af": {"100.0": [99.9]}, "pin": null, "linkage": 0} ], // 14A (Varianten 0-4, 5-8, 12-14), 14B per ctl eon_ta
      "level_khz": 2.0, "phase_deg": 0,                                        // RDS-Hub 0..7,5 kHz; 0 oder 90 Grad zum Pilot
      "group_version": "A", "rt_version": "A",                                 // 0A/0B, 2A/2B
      "rates": { "1a": 60, "3a": 20, "10a": 10, "11a": 30, "14a": 8, "15a": 20 }, // Sondergruppe alle n Gruppen
      "rt_from_tags": true                                                     // Playlist: RT + RT+ (ARTIST/TITLE) bei Titelwechsel
    },
    "second_carrier": {                      // zweites Programm digital addiert (Nachbarkanal/Capture-Test)
      "enable": false, "offset_khz": 200, "level_db": -10,                     // |df| 20..1800 kHz, Pegel relativ zum Nutzträger
      "deviation_khz": 75, "stereo": true, "preemphasis_us": 50, "pilot_khz": 6.75,
      "audio": { "source": {...} }, "rds": { "pi": "0xD3C5", "ps": "ZWEITER", ... }
    }
  },
  "rf": { "freq_hz": 100000000, "sample_rate": 4096000, "backoff_db": 3, "atten_db": 60, "enabled": false }
  // backoff_db: Trägeramplitude unter Vollaussteuerung (konstante Hüllkurve); mit Zweitträger gilt a1 + a2 = Vollaussteuerung - backoff
}
```

- Pegelbezug: 0 dBFS Audio **nach** Preemphasis = 100 % Audiohub. Ein 1-kHz-Ton mit −0,5 dBFS (+0,42 dB Preemphasis) moduliert
  praktisch voll. Testton 40 % Hub (IEC 60315-4): `level_dbfs: -8.3`.
- `ctl` (UKW): `{"rds":{"ps","rt","rtplus","ta","tp","pty","ms","ct","ptyn","af","lps","eon","eon_ta":{"pi","active"},"rt_auto":true}}`,
  `{"fm":{"audio_gain_db","mute","deviation_khz","pilot_khz","rds_level_khz","mpx_power_dbr"}}`,
  `{"second_carrier":{"level_db","enable"}}`, `{"playlist":{"action":"next|prev|jump","index":n}}`. Die DAB-Schlüssel `dls`
  (→ RT + RT+ aus `dlplus`), `pty` und `label` (→ PS) werden abgebildet. Antwort `{"ok":true,"done":[..],"rds":{Sollwerte}}`.
- `check` (UKW): `render` 4,096 MS/s s16 → FM-Demodulation in numpy → Hub (Spitze, Audioanteil), Pilot (Hub, Frequenz),
  57-kHz-Phase, RDS-Hub, 38-kHz-Rest, MPX-Leistung (dBr), Stereo-Decoder (Ton, Pegel, SINAD, Kanaltrennung), Blockgrenzen-
  Spektrallinien → redsea (PI, PS, RT, RT+, PTY, TP/TA, MS, DI, AF A/B, ECC/Land, CT, PTYN, LPS, EON, BLER).
  Bericht: `{"ok","mode":"fm","measured":{..},"rds":{..},"expected":{..},"fields":[{"name","soll","ist","ok"}],"mismatches":[..],
  "second_carrier":{..}}`. `decode --iq f [--format s16|cf32 --rate N --config c]` wertet Mitschnitte aus; ohne `--config` erkennt
  der Runner UKW-Mitschnitte automatisch (schmalbandig).

## Ergänzung 23.10.: DVB-T / DVB-C (`mode: "dvbt" | "dvbc"`) – Runner-Schema (verbindlich, Paket `runner/dvb/`)

Kette: `ffmpeg` (Quellen, Codecs, PAT/PMT, `-muxrate` = Nutzbitrate) → `tsp` (TSDuck 3.45: NIT/SDT/EIT/TDT/TOT einfügen,
`pcradjust`, bei UDP `regulate`) → Ausgabe: **stdout TS** | **UDP** (`tsp -O ip`) | **Modulator** (`dvbt_mod`/`dvbc_mod`, IQ int16 auf stdout).
`dablab_run.py` delegiert alle Befehle an `runner/dvb/`, sobald `mode` `dvbt`/`dvbc` ist (bei `ctl`: `session.json.standard`).

### Konfiguration – Runner-Format (das Dashboard-Format wird ebenfalls angenommen, siehe unten)

```jsonc
{
  "mode": "dvbt",
  "dvbt": { "mode": "2k"|"8k", "constellation": "QPSK"|"16-QAM"|"64-QAM", "code_rate": "1/2"|"2/3"|"3/4"|"5/6"|"7/8",
            "guard": "1/4"|"1/8"|"1/16"|"1/32", "bandwidth_mhz": 5|6|7|8, "cell_id": 0 },          // nur nicht-hierarchisch
  "dvbc": { "constellation": "16-QAM"|"32-QAM"|"64-QAM"|"128-QAM"|"256-QAM", "symbol_rate": 6900000, "sps": 2|1 },
  "ts": { "transport_stream_id": "0x0101", "original_network_id": "0xFF01", "network_id": "0xFF01",   // Labor: ONID 0xFF00–0xFFFF
          "network_name": "DABLAB", "lcn": true, "eit": true, "tdt": true, "country": "DEU", "utc_offset_min": "auto",
          "pmt_start_pid": "0x1000", "start_pid": "0x0100", "eit_bitrate": 30000, "tdt_bitrate": 2000, "pcr_period_ms": 30 },
  "services": [{
    "key": "tv1", "service_id": "0x0001", "name": "LAB TV 1", "provider": "DABLAB", "lcn": 1, "visible": true,
    "type": "tv"|"radio",                                   // service_type: TV SD 0x01, H.264-HD 0x19, Radio 0x02 (AAC-Radio 0x0A)
    "video": { "codec": "mpeg2"|"h264", "size": "sd"|"hd"|"720p"|"WxH", "fps": 25, "aspect": "16:9",
               "bitrate_kbps": "auto"|5000, "gop": 12, "preset": "veryfast", "interlaced": false },
    "audio": { "codec": "mp2"|"aac"|"ac3", "bitrate_kbps": 192, "channels": 2, "samplerate": 48000, "language": "deu" },
    "source": { "kind": "testpattern"|"file"|"youtube"|"slideshow"|"playlist"|"schedule",
                "pattern": "testsrc2"|"smptehdbars"|"smptebars"|"pal75bars"|…, "text": "LAB TV 1", "timestamp": true,
                "file": "media/video/x.mp4", "url": "https://youtu.be/…", "max_height": 1080, "download": true,
                "images": ["media/slide1.jpg"], "interval_s": 5, "playlist": "videoliste", "shuffle": false,
                "loop": true, "loop_mode": "feeder"|"stream_loop", "seek_s": 0, "normalize": false,
                "entries": [{ "time": "20:15", "days": [1, 2, 3, 4, 5], "kind": "file"|"playlist"|"testpattern"|"folder",  // schedule
                              "file": "…", "playlist": "…", "pattern": "smptebars", "folder": "C:\\Infoscreen",
                              "title": "…", "text": "…", "loop": true }], "filler": "smptebars",
                "audio": "source" | { "kind": "sine"|"speech"|"sine+speech"|"silence"|"noise"|"file"|"source",
                                      "freq_hz": 1000, "level_dbfs": -18, "text": "…", "file": "media/x.mp3" } },
    "eit": { "title": "…", "text": "…", "duration_min": 60, "next_title": null, "next_text": "", "language": "deu" }
  }],
  "output": { "kind": "stdout"|"udp"|"modulator"|"file", "host": "192.168.2.1", "port": 1234, "burst": 7, "rtp": false,
              "file": "media/out.ts", "modulator_cmd": null },   // modulator_cmd: Liste mit Platzhaltern {mode} {constellation} {code_rate} …
  "rf": { "channel": "K21"|"E5"|"S21"|"custom", "freq_hz": 474000000, "atten_db": 60, "backoff_db": 12, "enabled": false }
}
```
- Vom Runner gesetzt: `rf.sample_rate` = BW·8/7 MHz (DVB-T) bzw. `sps`·SR (DVB-C); `rf.freq_hz` aus dem Kanal, falls leer;
  `dvbt.bandwidth_mhz` aus dem Kanal (E = 7 MHz, K = 8 MHz), falls leer.
- `bitrate_kbps: "auto"` verteilt die Nutzbitrate (abzüglich SI ≈ 250 kbit/s + EIT/TDT, Audio, 4 % Reserve) nach Pixelzahl,
  gedeckelt (MPEG-2 SD 9 / HD 15 Mbit/s, H.264 SD 6 / HD 20 Mbit/s). Überbuchung → Warnung.
- PIDs: PMT = `pmt_start_pid + i`, Video = `start_pid + 16·i`, Audio = Video + 1; PCR auf dem Video (Radio: Audio).
- Playlist-Quelle: Playlists wie bei DAB (`runtime/playlists/<name>.json`, `dablab_run.py playlist import …`), Einträge sind Videos.
  Jeder Titel wird nahtlos in dieselben Rohdaten-FIFOs dekodiert (Bild/Ton gleich lang aufgefüllt); EIT present/following = aktueller/nächster Titel.
- Zeitplan-Quelle (`schedule`, TV und Radio): jeder Eintrag läuft ab `time` (Ortszeit `HH:MM[:SS]`; `days` 1 = Mo … 7 = So, leer =
  täglich) bis zum nächsten Start; Datei/Playlist in Schleife, `loop: false` → danach Füller-Testbild (`filler`). Umgeschaltet wird
  bildgenau nach der Stromuhr in denselben Rohdaten-FIFOs wie bei der Playlist (kein Neustart, PTS/PCR stetig); startet die Kette mitten
  in einem Eintrag, springen Datei/Playlist an die passende Stelle. EIT present/following aus dem Zeitplan (Start/Dauer wie geplant,
  `event_id` aus der Startzeit) und **EIT schedule actual** (0x50, Segmente zu 3 h, 48 h voraus, alle 10 s, DATV 30 s); SDT
  `EIT_schedule = true`. Dashboard: `video.source.kind = "schedule"`, Einträge in `video.source.schedule[]`, `video.source.filler`.
- **Dashboard-Format** (`web/js/standards.js`) wird angenommen und intern übersetzt: `dvbt.fft` → `dvbt.mode`; `dvb.si`
  {`network_id`,`network_name`,`onid`,`tsid`,`lcn`,`eit`,`tdt`} → `ts`; `dvb.programs[]` {`key`,`name`,`provider`,`sid`,`lcn`,`type`,
  `video{codec,resolution,bitrate_kbps,source{kind,pattern,file,url,playlist,loop}}`,`audio{codec,bitrate_kbps,source{kind,…}}`,
  `eit{title,description,duration_min}`} → `services[]`. `resolution` `hd720` → 1280×720, `hd` → 1920×1080. YouTube mit `source.file`
  wird als Datei gespielt, sonst wird `url` vorher geladen. `rf.path` `"pluto"` → `output.kind = "udp"` an 192.168.2.1:1234, sonst `"modulator"`.
  ⚠️ **Abweichungen vom Dashboard:** (1) `hd` wird **progressiv** (1080p25) kodiert, der Dashboard-Text nennt 1080i; `video.interlaced: true`
  wirkt nur bei MPEG-2. (2) Pattern `pm5544` → `pal75bars`, `color` → `rgbtestsrc` (ffmpeg kennt kein PM5544). (3) Tonquelle `tone` = Sinus,
  auch bei Videodateien; Ton aus der Datei mit `audio.source.kind = "video"` (Dashboard) bzw. `"source"` (Runner).

### Nutzbitrate (= `ffmpeg -muxrate` und `tsp --bitrate`, exakt als Bruch gerechnet)

- DVB-T (EN 300 744): R = b · CR · 188/204 · 1512/2048 · (BW · 8/7 MHz) / (1 + GI), b = 2/4/6; 2k und 8k gleich.
  8 MHz/64-QAM/2/3/1/32 = 24 128 342,25 b/s, 6 MHz = ×6/8 (18 096 256,7 b/s). Alle Werte: `dablab_run.py media rates dvbt`.
- DVB-C (EN 300 429): R = SR · log2(M) · 188/204; 6,9 MBd/256-QAM = 50 870 588,2 b/s, 3,45 MBd/256-QAM = 25 435 294,1 b/s.
- ffmpeg bekommt die gerundete Ganzzahl (Fehler < 0,01 ppm), `pcradjust` rechnet alle PCR auf die exakte Rate um.

### Kanaltabellen (Mittenfrequenz MHz; `dablab_run.py media channels dvbt|dvbc`)

- DVB-T: Band III **E5–E12** (7 MHz): 177,5 + 7·(n−5) → E5 177,5 … E12 226,5. Band IV/V **K21–K69** (8 MHz): 474 + 8·(n−21) → K21 474 … K69 858.
- DVB-C (deutsches 8-MHz-Digitalraster): Sonderkanäle **S02–S09** 113 … 169; **K05–K11** 177 … 225; **S11–S20** 233 … 305;
  Hyperband **S21–S41** 306 … 466; **K21–K69** 474 … 858 (wie DVB-T); Band I K02–K04 (50,5/57,5/64,5, 7-MHz-Raster). S10/K12 entfallen
  im 8-MHz-Raster. ⚠️ Den VHF-Teil (S02–S20, K05–K11) gegen die Liste des Netzbetreibers prüfen (manche Netze liegen 1 MHz höher,
  z. B. 114/122 MHz) – dann `rf.channel = "custom"` + `rf.freq_hz`.

### SI-Tabellen

PAT/PMT (ffmpeg; NIT-PID 16 in der PAT; Stream-Typen 0x02/0x1B/0x03/0x0F, AC-3 als 0x06 + AC-3-Deskriptor) · SDT actual (service_descriptor,
EIT-p/f-Flag; `inject --replace --poll-files`) · NIT actual (network_name, terrestrial_ bzw. cable_delivery_system_descriptor mit Frequenz/
Modulation/Symbolrate/FEC, service_list, private_data_specifier 0x28 + EACEM-LCN 0x83) · EIT p/f actual (eigene binäre Abschnitte
0 = present, 1 = following; Version zählt bei jeder Änderung) · TDT/TOT (`timeref --system`, local_time_offset DEU) · Nullpakete auf
konstante Rate (ffmpeg `muxrate`) · PCR-Korrektur (`pcradjust`). Teletext/DVB-Untertitel: nicht umgesetzt.

### Befehle

| Befehl | Wirkung |
|---|---|
| `live --config c.json` | stdout je nach `output.kind`: TS (`stdout`), IQ int16 (`modulator`, Rate `rf.sample_rate`), nichts (`udp`, `file`). stderr wie bei DAB: erste Zeile `{"session"}`, dann JSON-Zeilen, alle 5 s `{"msg":"status","uptime_s","out_bytes","out_rate_bps","target_bps","procs",["level"],["now_playing"]}` |
| `render --config c.json --seconds N --out f [--format ts\|s16]` | TS-Datei (Standard) oder IQ über den Modulator; JSON-Zusammenfassung |
| `check --config c.json [--seconds 20] [--keep]` | rendert einen TS und prüft ihn (s. u.); Bericht auch in `runtime/checks/dvb_<sid>.json` |
| `validate --config c.json` | Fehler, Warnungen, Nutzbitrate, Budget, PIDs |
| `ctl --session <id> '<json>'` | `{"eit":{"service","title","text",["next_title","next_text","clear"]}}`, `{"name":{"service","name","provider"}}` (SDT-Version +1), `{"lcn":{"service","lcn"}}` (NIT-Version +1), `{"playlist":{"service","action":"next\|prev\|jump","index"}}`, `{"status":true}`. Ohne Neustart: `tsp` lädt die Tabellendateien per `--poll-files` nach |
| `media youtube <url> [--max-height 1080] [--force]` | yt-dlp → `runtime/media/video/<id>.mp4` (H.264/AAC bevorzugt, ≤ max-height); stderr `{"msg":"youtube","youtube":{"status","percent","downloaded_bytes","total_bytes","speed_bps","eta_s","file"}}`, stdout `{"ok","file","id","title","duration_s","width","height","url","license","cached"}`; Index `media/video/youtube_index.json` |
| `media list \| info <datei> \| channels dvbt\|dvbc \| rates dvbt\|dvbc \| tools` | Videobibliothek, ffprobe, Kanal- und Ratentabellen, Werkzeugversionen |

Hinweis für den Server (Dashboard-Annahme `GET /api/youtube/<job>`): `speed` = `speed_bps`, `status` `downloading` → `running`, `finished` → `done`.

### Prüfbericht (`check`), kompatibel zu `web/js/report_std.js`

```
{ok, lock:true, t, bitrate_bps, pcr_jitter_ns_max, continuity_errors, modulation{…NIT-Delivery…}, tsid,
 nit{network_id,network_name,onid,tsid,version,delivery{kind,bandwidth_mhz|symbol_rate,constellation,code_rate,guard,fft,frequency_hz,…},private_data_specifier},
 lcn{"0x0001":1}, tables{pat,pmt{sid:{pcr_pid,components}},sdt,nit,eit[],tdt,tot}, table_counts,
 services[{sid,name,provider,type:"tv"|"radio",service_type,lcn,video_codec,resolution,audio_codec,video_bitrate_kbps,audio_bitrate_kbps,
           eit_title,eit_description,pids{pmt,pcr,video,audio},decode{rc,video_frames,decoded_s,errors},ffprobe,stream_check[]}],
 ffprobe{ok,streams[{sid,pid,type,codec,width,height,fps,samplerate,channels}]},
 bitrate{target_bps,measured_bps (PCR-Regression),deviation_pct,tsanalyze_bps,null_share,max_jitter_ns}, pcr{pids{…}}, continuity{…},
 tsanalyze{…}, mismatches[…], summary, report_file}
```
⚠️ Ohne HF gibt es kein `mer_db`/`ber_*`; `lock` ist bei der TS-Prüfung immer `true`. MER/BER kommen erst aus RX-Mitschnitten (offen).

### Ausgabewege

- **UDP** (Modulator auf dem Pluto): `tsp -P regulate --bitrate R -O ip host:port --packet-burst 7 --enforce-burst` (7 × 188 = 1316 Byte je Datagramm, optional RTP).
- **Modulator am PC** (USB-Streaming): `dvbt_mod -i - -o - --mode --const --rate --guard --bw --backoff [--cell-id]` bzw.
  `dvbc_mod -i - -o - --qam --sr --sps --backoff` (gesucht in `$DABLAB_DVB_MOD_DIR`, `/opt/dablab/mod/src`, `pluto_fw/mod`). IQ int16 LE auf stdout,
  getaktet vom Leser (keine Echtzeitbremse im Runner). Per USB passen DVB-T 6 MHz (6,857 MS/s) und DVB-C 3,45 MBd (6,9 MS/s).

### Nachtrag 23.10. (DVB): nahtlose Schleife, HD-Transkodierung

- **Schleife** (`source.loop: true`, Datei/YouTube): Standard `loop_mode: "feeder"` – die Datei wird Durchlauf für Durchlauf in
  Rohdaten-FIFOs dekodiert (wie die Video-Playlist), der Ton je Durchlauf exakt auf die Bildlänge gebracht (`apad` + `-t` = Videodauer).
  Der Mux sieht einen endlosen Strom mit festem Takt: PTS/DTS/PCR laufen ohne Sprung weiter, kein `discontinuity_indicator` nötig,
  Bild und Ton bleiben über beliebig viele Durchläufe synchron. `"stream_loop"` (alter Weg, `ffmpeg -stream_loop -1`) bleibt wählbar.
  Tonquellen aus Dateien laufen über `amovie=…:loop=0,asetpts=N/SR/TB`, die Diashow als ausgeschriebene concat-Liste (≥ 48 h).
- **HD live**: H.264 1080p25 `-preset veryfast` schafft in WSL1 mit 4 Kernen 2,46-fache Echtzeit (Quelle 1080p60, 8 Mbit/s),
  ein Vorab-Transkodieren ist nicht nötig.

## Ergänzung 23.10.: Analog-TV PAL (`mode: "pal"`, Paket `runner/pal/`)

Generator von `pluto_fw/mod/tools/pal_bg_gen.py` übernommen (am LG-Fernseher erprobt; bei gleichem Bild **bitgleiche** Ausgabe,
`runner/tests/pal_port_check.py`). Negative AM mit Restseitenband, PAL-Farbe (Burst ±135°, V-Schalter), FM-Ton mit 50-µs-Preemphase.

```jsonc
{
  "mode": "pal",
  "pal": {
    "system": "BG"|"DK"|"I",                 // Tonträger 5,5 / 6,5 / 6,0 MHz, Restseitenband 0,75 / 0,75 / 1,25 MHz
    "fs": 8000000|7000000,                   // Schleife (render/check): Standard 8 MS/s
    "live_fs": 7000000,                      // live: 7 MS/s (8 MS/s gehen nicht durch USB, gemessen max. 7,15 MS/s)
    "offset_hz": "auto",                     // Bildträger liegt bei −offset im Basisband; auto: B/G 2,25 MHz (8 MS/s) bzw. 2,1 MHz (7 MS/s),
                                             // D/K 2,675 MHz, I 2,175 MHz; Vielfaches von 25 Hz (ganze Perioden je Vollbild)
    "level": 0.9,                            // Spitze relativ zur Vollaussteuerung (Synchronboden + Ton)
    "image": { "kind": "testpattern"|"file"|"video"|"youtube", "pattern": "pal75bars", "file": "media/…", "url": "…",
               "text": "DABLAB Test", "text2": "PAL B/G K36", "timestamp": true },   // timestamp: Uhr + Bildzähler (live)
    "sound": { "kind": "tone"|"silence"|"off"|"file"|"source", "freq_hz": 1000, "deviation_hz": 50000,
               "level_db": -13, "preemphasis_us": 50, "tone_level": 1.0, "file": "media/x.mp3" }  // level_db: Ton/Bild bezogen auf die Synchronspitze
  },
  "rf": { "channel": "K36"|"E5"|"S21"|"custom", "freq_hz": 591250000, "atten_db": 60, "enabled": false }   // freq_hz = BILDTRÄGER
}
```
- Vom Runner gesetzt: `rf.lo_hz` = Bildträger + Versatz (Schleife), `rf.lo_hz_live` (live), `rf.sample_rate` = `pal.fs`.
  ⚠️ **Für den Server**: bei `mode: "pal"` den Pluto-LO auf `lo_hz` bzw. `lo_hz_live` stellen, **nicht** auf `freq_hz`.
- Kanäle (Bildträger MHz): E2–E4 48,25/55,25/62,25; E5–E12 = K05–K12 175,25 + 7·(n−5); K21–K69 471,25 + 8·(n−21);
  Kabel S02–S10 112,25 + 7·(n−2), S11–S20 231,25 + 7·(n−11), S21–S41 303,25 + 8·(n−21).
- Schleife: 4 Vollbilder = 8 Halbbilder = 160 ms = 1 280 000 Samples (8 MS/s, 5,12 MB int16) – Farbträger, Mischer und Tonträger
  haben darin ganze Periodenzahlen, der Ton wird auf ein Vielfaches von 6,25 Hz gerundet. Passt in den zyklischen Puffer (16 MiB).

| Befehl | Wirkung |
|---|---|
| `render --config c.json --out f [--format s16\|cf32] [--method fft\|stream]` | Schleife als Datei für den zyklischen Puffer; JSON mit `samples`, `sample_rate`, `lo_hz`, Spektrum (Ton, Farbträger, Restseitenband) |
| `check --config c.json [--method both\|fft\|stream]` | Schleife erzeugen (int16 wie gesendet), über die Schleifenstelle demodulieren → `runtime/checks/pal_<sid>_<verfahren>.png` + Bericht `pal_<sid>.json`: Zeilenfrequenz, Austast-/Weißpegel relativ zur Synchronspitze (Soll 0,73/0,10), Ton/Bild (Soll `level_db`), Tonfrequenz und Hub (FM-Demodulation), Farbträgerpegel, Korrelation mit dem Sollbild, Schleifenstelle, Clipping; `mismatches` |
| `live --config c.json [--workers 1-3]` | bewegtes Bild + Ton, IQ int16 auf stdout mit `live_fs` (getaktet vom Leser); stderr: `{"session"}`, Info mit `sample_rate`/`lo_hz`, alle 5 s `status` (frames, fps, realtime, Pegel, Clips) |
| `validate --config c.json` | Prüfung + Zusammenfassung (LO, Raten, Ton-Frequenz) |

Live-Encoder: ffmpeg liefert Bilder (yuv444p, 25 fps) und Ton (48 kHz); je Vollbild Composite bei 2·fs, komplexes FIR
(Restseitenband + Verschiebung + 2:1), mehrere Prozesse (Vollbilder unabhängig, weil Zeilen 623–625 reine Synchronzeilen sind);
FM-Ton im Hauptprozess mit stetiger Phase. Durchsatz (7 MS/s, WSL1): 1 Prozess 0,72–0,80×, 2 Prozesse 1,24×, 3 Prozesse 1,61–1,79× Echtzeit.

## Ergänzung 23.10.: GPU-Encoding (NVENC), DVB-S, Windows-Quellen, YouTube-Cookies

### Schema-Erweiterungen (DVB, Runner-Format)

```jsonc
"mode": "dvbs",                                   // neu, zusätzlich zu dvbt/dvbc
"dvbs": { "symbol_rate": 22000000, "code_rate": "1/2"|"2/3"|"3/4"|"5/6"|"7/8",
          "frequency_hz": 11038000000,            // Downlink fuer die NIT (satellite_delivery_system_descriptor)
          "orbital_position": "19.2", "west_east": "east", "polarization": "vertical"|"horizontal"|"left"|"right", "roll_off": "0.35" },
          // Nutzrate = SR · 2 · CR · 188/204 (22 MBd, 7/8: 35 480 392,2 b/s); rf.sample_rate = SR (dvbs_mod --sps 1)
          // DVB-S2 (EN 302 307-1, CCM, Normal-FECFRAME, ab 23.10.): zusätzlich
          //   "system": "dvbs2", "modulation": "QPSK"|"8PSK", "pilots": true|false, "roll_off": "0.35"|"0.25"|"0.20",
          //   "code_rate": QPSK 1/4 … 9/10, 8PSK 3/5, 2/3, 3/4, 5/6, 8/9, 9/10
          //   Nutzrate = SR · (Kbch − 80) / PLFRAME-Symbole (22 MBd QPSK 3/4 Pilots: 31 945 676,3 b/s) → dvbs2_mod auf dem Pluto
          //   NIT: modulation_system "DVB-S2", FEC_inner 1/4, 1/3, 2/5 als "undefined" (EN 300 468 kennt sie nicht)
          //   Pluto-Grenze (ein A9-Kern mit Ausgabe): QPSK 22 MBd, 8PSK 15 MBd (Warnung darüber)
"services[].video": {
  "codec": "mpeg2"|"h264"|"hevc",                 // hevc neu (CPU: libx265, GPU: hevc_nvenc); service_type HEVC = 0x1F
  "encoder": "cpu"|"nvenc",                       // nvenc: Windows-ffmpeg (C:\ffmpeg\bin\ffmpeg.exe, DABLAB_WIN_FFMPEG) aus WSL heraus
  "size": "sd"|"hd"|"720p"|"uhd"|"WxH",           // uhd = 3840x2160
  "bitrate_kbps": 16000,                          // NVENC: CBR mit VBV (bufsize = vbv_s · Bitrate, Standard 0,5 s)
  "nv_preset": "p4", "vbv_s": 0.5, "win_threads": 4, "bit_depth": 8|10,
  "hdr": { "mode": "off"|"hdr10"|"hlg",           // nur HEVC + nvenc: Main10, p010le, BT.2020, PQ (smpte2084) bzw. HLG (arib-std-b67)
           "inject_sei": "auto"|"always"|"never", // HDR10-SEI: auto = aus der Quelle (NVENC reicht sie durch), fehlen sie, setzt der Runner sie ein
           "master_display": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)", "max_cll": 1000, "max_fall": 400 } },
"services[].source.kind": … | "dshow" | "gdigrab" | "ddagrab" | "ts",
   // dshow: {"device": "<Name aus media dshow>", "dshow_options": [...]}, Ton optional {"audio": {"kind": "dshow", "device": "..."}}
   // gdigrab: Bildschirm {"region": [x, y, w, h]} oder {"window": "Fenstertitel"}; ddagrab: {"output": 0}
   // dshow/gdigrab/ddagrab nur mit video.encoder = nvenc (laufen auf Windows)
   // ts: fertigen Transportstrom uebernehmen {"file": "...ts", "program": N, "loop": true} (Video/Ton kopiert)
"services[].source": { …, "cookies": "runtime/cache/youtube_cookies.txt", "cookies_from_browser": "edge" }   // YouTube
"service_type": "0x1F"                            // optional: service_type erzwingen
```

### NVENC-Weg

- Je Dienst ein Windows-ffmpeg: Quelle → (CUDA-Dekodierung für H.264/HEVC/VP9/MPEG-2) → `fps=25`, Skalierung, `format=yuv420p|p010le` →
  `hevc_nvenc`/`h264_nvenc -preset p4 -tune hq -rc cbr -b:v B -maxrate B -bufsize B·0,5 -g 25 -keyint_min 25 -forced-idr 1
  -force_key_frames expr:gte(t,n_forced*1) -bf 0 -aud 1 -spatial-aq 1` + Farbkennung (`-color_primaries/-color_trc/-colorspace` und
  `-bsf:v hevc_metadata|h264_metadata` für die VUI) → **roher Elementarstrom auf stdout** → Pipe (WSL-Interop) → Relais (optional
  HDR10-SEI-Einfügung) → Mux-ffmpeg (`-f hevc -framerate 25 -fflags +genpts`, `-c copy`) → tsp (SI, PCR, Stopfen, regulate) wie bisher.
  Zeitstempel kommen aus der festen Bildrate: lückenlos, auch über Schleifen.
- Datei endlos: ffconcat-Liste mit derselben Datei, je Eintrag `outpoint` = Videodauer – Windows (Bild) und WSL (Ton) schneiden an derselben Stelle.
- Pfade werden umgerechnet: `/mnt/g/…` → `G:\…`, andere Linux-Pfade → `\\wsl$\dablab\…`.
- Wachhund: liefert ein Windows-Encoder 30 s lang keine Daten (z. B. Kamera nicht aktiv), bricht `live`/`check` mit Meldung ab.
  Beim Stoppen bekommt das Windows-ffmpeg `q` auf stdin, notfalls wird es beendet (geprüft: kein ffmpeg.exe bleibt übrig).
- ⚠️ Grenzen der GTX 1080 (Pascal): HEVC-NVENC ohne B-Frames (`-bf 0`), kein AV1-Dekoder (AV1-Quellen vorher umwandeln; das gyan-essentials-
  ffmpeg hat kein libdav1d → klare Fehlermeldung), H.264 und HEVC 8/10 bit werden auf der GPU dekodiert.

### YouTube-Cookies (gegen „Sign in to confirm you're not a bot“)

1. **Cookie-Datei** (empfohlen): im Browser mit einer Erweiterung „cookies.txt“ (Netscape-Format) für youtube.com exportieren, als
   `runtime/cache/youtube_cookies.txt` ablegen – wird danach automatisch verwendet; oder je Aufruf `media youtube <url> --cookies <datei>`
   bzw. `source.cookies`.
2. **Aus dem Browser** (nur mit ausdrücklicher Freigabe): `dablab_run.py media youtube-consent edge|firefox|chrome|…` legt
   `runtime/cache/youtube_browser_consent.json` an. Erst danach liest `media youtube <url> --cookies-from-browser edge` (bzw.
   `source.cookies_from_browser`) einmalig über das Windows-yt-dlp (`yt-dlp.exe` der Python-Installation des Nutzers, `DABLAB_WIN_YTDLP`) die
   Cookies und schreibt sie in `runtime/cache/youtube_cookies.txt`. Widerruf: `media youtube-consent off` (löscht Freigabe und Cookie-Datei).
   Ohne Freigabedatei wird nie aus dem Browser gelesen. Chrome ≥ 127 verschlüsselt Cookies app-gebunden – Edge oder Firefox nehmen.

### Weitere Befehle

`media dshow` (DirectShow-Geräte über das Windows-ffmpeg), `media gpu` (nvidia-smi: Modell, Treiber, laufende Encoder-Sitzungen),
`media youtube <url> [--cookies f] [--cookies-from-browser b]`, `media youtube-consent <browser|off>`.
