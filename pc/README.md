# DAB-Lab – PC-Server

Weboberfläche + HTTP-API (siehe `../SPEC.md`) und TX-Streaming zum ADALM-Pluto über libiio/USB.

⚠️ Nur abgeschirmt senden. Der Pluto startet stumm (−89,75 dB). Fehler, `tx/stop` und Serverende schalten ihn ebenfalls stumm.
Das gilt auch beim Schließen des Konsolenfensters (Ctrl-Handler). Ein hartes `taskkill /F` umgeht das. Danach mit
`iio_attr -u usb: -c -o ad9361-phy voltage0 hardwaregain` prüfen.

## Start

```
start_dablab.cmd                     → http://127.0.0.1:8792/
start_dablab.cmd --lan               zusätzlich im LAN (0.0.0.0, ohne Anmeldung!)
start_dablab.cmd --min-atten 60      Sicherheitsklemme: Dämpfung nie unter 60 dB
```

| Option | Standard | Bedeutung |
|---|---|---|
| `--backend wsl\|ssh\|local` | `wsl` | Ort des Runners: WSL2-Distribution `dablab` (`runner\dablab_run.py`, `DABLAB_RUNTIME=/mnt/g/.../runtime`) oder Mac per ssh (`~/dablab/runtime/dablab_run.py`) |
| `--source auto\|runner\|testgen` | `auto` | `auto`: Runner, sobald er installiert ist **und** `selftest` ok meldet, sonst Ersatzgenerator `testgen.py`. Das Umschalten passiert ohne Codeänderung beim nächsten `tx/start` |
| `--uri` | `usb:` | libiio-URI des Pluto |
| `--min-atten` | `0` | untere Grenze der Dämpfung (zusätzlich zu 0…89,75 dB) |
| `--no-nco` | aus | Frequenzversatz über den LO statt über den NCO. `lo_offset_hz` wird dann ignoriert |
| `--nsamp` / `--kbufs` | 2^17 / 8 | TX-Puffergröße und Anzahl Kernel-Puffer (256 ms im Pluto, dazu 512 ms Warteschlange am PC) |
| `--no-auto-restart` | aus | Nach einem Pluto-Fehler TX nicht automatisch neu starten (sonst höchstens 5 Neustarts in 10 min) |
| `--rx-uri` / `--rx-mode` | `ip:192.168.2.1` / `auto` | RX-Mitschnitt für `/api/monitor` läuft in einem **Kindprozess mit eigenem iio-Kontext** (Standard über RNDIS), damit TX auf `usb:` keine Underruns bekommt. `auto`: bei Fehler In-Prozess-Mitschnitt; `proc`: nur Kindprozess; `inproc`: alter Weg |
| `--no-priority` | aus | Prozess bleibt NORMAL; sonst ABOVE_NORMAL und TX-Schubthread HIGHEST |
| `--runtime <dir>` | `../runtime` | eigener runtime-Ordner (Konfiguration, Medien, Playlists, Mitschnitte), z. B. für eine Testinstanz |
| `--wsl-distro`, `--ssh-host` | `dablab`, leer (`DABLAB_SSH_HOST`, z. B. `user@mac.local`) | |

Die Optionen gehen auch über Umgebungsvariablen: `DABLAB_BACKEND`, `DABLAB_SOURCE`, `DABLAB_MIN_ATTEN`, `DABLAB_PLUTO_URI`.

## Dateien

- `server.py`: ThreadingHTTPServer, liefert `web/` aus und setzt die API um, einschließlich Validierung (CU-Kapazität mit UEP-Tabelle aus ODR-DabMux), SSE, Presets, Upload, check, monitor und sweep.
- `pluto.py`: libiio-Zugriff (`Pluto`) und `TxStream` (Lesethread holt die Daten aus der stdout des Runners in eine begrenzte Warteschlange, Schubthread schreibt sie per NCO in `iio.Buffer.push`).
- `iio_load.py`: lädt pylibiio mit der libiio-DLL (`add_dll_directory` + gepatchtes `find_library`) und unterdrückt `__del__`-Fehler.
  Suchreihenfolge: `DABLAB_LIBIIO_DIR`, `pc\libiio\` (gebündelt).
- `rxproc.py`: RX-Mitschnitt im Kindprozess (Aufnahme, Welch-Spektrum, 2:1-Dezimation, Testgenerator-Korrelation, cf32-Datei). Auch einzeln aufrufbar.
- `playlists.py`: Playlist-Speicher `runtime/playlists/<name>.json`, Import m3u/m3u8/pls, Export m3u8, Validierung.
- `agent.py`: Client für dablab-agent und Befehlszeile für `dvbt_mod`/`dvbc_mod` auf dem Gerät.
- `standards.py`: Mehrstandard-Prüfung (UKW, DVB über den Runner-Normalisierer), Wahl des Ausgabewegs, ffprobe-Medienindex.
- `channels.json`: Kanaltabellen DAB/UKW/DVB-T/DVB-C (DVB aus `runner/dvb/tables.py`, wie SPEC).
- `tests_fake/`: `fake_runner.py` (Runner-Attrappe: live UDP/IQ, `media youtube`) und `mock_agent.py` (dablab-agent-Attrappe mit UDP-Zähler).
- `test_device_offline.py`: DVB-Prüfung, Gerätepfad gegen den Mock-Agenten, fremder Modulator, YouTube-Jobs, Video-Upload – ohne Hardware.
- `test_api_offline.py`: Tests ohne Hardware (`--http` startet eine Instanz auf freiem Port mit `--uri ip:127.0.0.1`).
- `test_monitor_underruns.py`: misst Underruns am Sender während `/api/monitor`-Mitschnitten gegen einen laufenden Server.
- `testgen.py`: Ersatz für `dablab_run.py live`. Erzeugt OFDM wie DAB Mode I (1536 QPSK-Träger, 1,536 MHz, Schutzintervall, Nullsymbol) als int16 auf stdout, blockierend.
- `presets/*.json`: Presets. `../runtime/pc_config.json` ist die aktuelle Konfiguration und zugleich die Eingabe für den Runner.
- `../runtime/media/`: Medien. Upload schreibt dorthin, beim ssh-Backend zusätzlich nach `~/dablab/runtime/media/`.
- `../runtime/rx/`: Monitor-Mitschnitte `mon_*.cf32.iq` mit 2,048 MS/s. Die letzten 5 bleiben liegen.

## Messwerte und Mechanik

- **Underruns** kommen direkt aus der Hardware. Das ist das DAC-Underflow-Bit von `cf-ad9361-dds-core-lpc` (Register 0x88, Bit 0, W1C), alle 0,25 s gelesen und gelöscht.
  `src_late` zählt, wie oft die Warteschlange beim Abholen leer war. Dann liegt die Ursache bei der Quelle, nicht bei USB oder Pluto.
- `rate_ms` ist die Rate über 5 s, `rate_avg_ms` der Mittelwert ab vollem Kernel-Puffer. Die Latenz ist Warteschlange + Kernel-Puffer.
- **Steuerzugriffe während des Streamings** (Attribute, Register) schlagen per USB selten mit EIO fehl, gemessen etwa 1 auf 5000.
  `pluto.py` wiederholt sie (`ctl_retries`). Erst wenn ein Zugriff viermal hintereinander scheitert, gilt der Pluto als getrennt.
- **iio_attr bei laufendem Server**: Der Server belegt `usb:` exklusiv. Zum Gegenlesen deshalb
  `iio_attr -u ip:192.168.2.1 -c -o ad9361-phy voltage0 hardwaregain` nehmen (RNDIS). Nach Serverende geht wieder `usb:`.
- **Monitor** (`/api/monitor`): RX-LO = Signalmitte (`freq_hz + offset_hz`), Verstärkung manuell, Mitschnitt mit 4,096 MS/s. Dabei läuft TX weiter.
  Der Mitschnitt läuft in einem Kindprozess (`rxproc.py`) mit eigenem Kontext über `ip:192.168.2.1`, RX-Puffer klein (2^16, 4 Kernel-Puffer), Kindprozess BELOW_NORMAL. Der Bericht nennt den Weg in `rx_path`.
  Der Selftest des Runners läuft nie, solange TX aktiv ist (er rendert einen Mux und kostet CPU).
  Der Bericht enthält das Welch-Spektrum (Band ±760 kHz gegen 0,9…1,72 MHz daneben, 512-Punkte-PSD fürs Dashboard).
  Danach `resample_poly` 2:1 auf 2,048 MS/s cf32 und `dablab_run.py decode`. Mit dem Testgenerator kommt zusätzlich `testgen_corr` hinzu:
  Korrelation mit dem bekannten Rahmen, etwa 50 dB Verarbeitungsgewinn, also auch unter dem Rauschen nachweisbar.

## Playlists (SPEC-Ergänzung 23.10.)

- `audio.source.kind = "playlist"` mit `playlist` (Name), `shuffle`, `loop`, `crossfade_s` (0–12), `normalize`, `dls_from_tags`. Die Validierung verlangt, dass die Playlist existiert.
- `GET /api/playlists` (Übersicht), `GET/PUT/DELETE /api/playlists/<name>`, `POST /api/playlists/<name>/import` (m3u/m3u8/pls-Text im Body, Pfade relativ zu `media/`), `GET /api/playlists/<name>/export` (m3u8).
- Upload mehrerer Dateien: `POST /api/upload?kind=audio[&playlist=<name>]` als `multipart/form-data`; jede Datei mit `filename`. Rohdaten-Upload mit `?name=` geht weiter. `playlist=` hängt die Dateien an die Playlist an (legt sie an).
- `POST /api/live {"playlist":{"service":"srv1","action":"next|prev|jump","index":n}}` → Runner `ctl`.
- `tx.now_playing`: je Dienst `{service,index,title,artist,file,elapsed_s,duration_s,count,playlist,t}`, aus den Runner-Statuszeilen (`now_playing` in einer stderr-JSON-Zeile; als Objekt je Dienst, Liste oder Einzelobjekt mit `service`). `t` ist die Empfangszeit am PC zum Hochrechnen von `elapsed_s`.

## Mehrstandard (`mode`: dab | fm | dvbt | dvbc)

- `mode` fehlt oder 1–4 → DAB (1–4 bleibt der DAB-Übertragungsmodus). `"fm"`, `"dvbt"`, `"dvbc"` wählen den Standard; `state.standard` zeigt ihn.
- **fm** (SPEC „UKW“): `rf.freq_hz` wird aus `fm.freq_mhz` abgeleitet (`rf.channel = "fm"`); 87,5–108 MHz ist Pflicht, außerhalb des 100-kHz-Rasters gibt es eine Warnung (`warnings` in der Antwort und `state.rf.warnings`). `rf.backoff_db` Standard 3 dB. Geprüft werden Hub/Pilot/Preemphasis, RDS (PI, PS ≤ 8, RT ≤ 64, PTY, AF, EON-PI, Hub) und Zweitträger. `/api/rf {"freq_hz"}` hält `fm.freq_mhz` synchron. Preset `UKW Standard`.
- **dvbt/dvbc** (SPEC „DVB-T / DVB-C – Runner-Schema“): geprüft mit dem Normalisierer des Runners (`runner/dvb/config.py`, direkt importiert), also identisch zum Runner – Runner-Format (`dvbt`/`dvbc`, `ts`, `services[]`) und Dashboard-Format (`dvbt.fft`, `dvb.si`, `dvb.programs[]`). Daraus: `rf.channel`/`rf.freq_hz` (E5–E12, K21–K69 bzw. Kabelraster), `rf.sample_rate` (BW·8/7 bzw. sps·SR), Nutzbitrate und Bitratenbudget in `state.dvb` (`rate_bps`, `budget`, `load_pct`, `overbooked`). Überbuchung (Programme + SI > Nutzbitrate) → Warnung „Überbuchung …“ (SPEC: Warnung, kein Fehler). Die Konfiguration selbst geht unverändert an den Runner. `GET /api/dvb/rates?standard=dvbt|dvbc` liefert die Ratentabelle.
- DAB-Prüfungen (Ensemble, Dienste, CU) laufen nur bei DAB.
- Kanaltabellen zentral in `pc/channels.json`, für das Dashboard unter `GET /api/channels` (auch `/channels.json`). `GET /api/standards` nennt Standards und die je Standard erlaubten Live-Schlüssel.
- `/api/check` und `decode` werden generisch durchgereicht (FM-Bericht hat `measured`/`rds`/`fields`/`mismatches`); `decode` bekommt `--config` und bei fm `--mode fm`.
- `/api/live` bei fm: `{"rds":{ps,rt,rtplus,ta,tp,pty,ms,ct,ptyn,af,lps,eon,eon_ta,rt_auto}}`, `{"fm":{audio_gain_db,mute,deviation_khz,pilot_khz,rds_level_khz,mpx_power_dbr}}`, `{"second_carrier":{...}}`, Playlist wie gehabt. Schlüssel, die nicht zum Standard passen, geben 400.
- **Ausgabeweg `rf.path`** (`state.rf.path`, dazu `path_label` „usb“/„pluto“ fürs Dashboard und `path_reason`): `rf.path` in der Konfiguration `auto` (Standard) | `usb` | `device` (Alias `pluto`).
  - `usb`: IQ per libiio vom PC (7,15 MS/s gemessen, Grenze 7,0 MS/s = 28 MB/s): DAB, UKW, DVB-T 6 MHz (6,857 MS/s), DVB-C ≤ 3,45 MBd mit sps 2. Für DVB setzt der Server `output.kind = "modulator"` (Runner ruft `dvbt_mod`/`dvbc_mod` am PC, IQ auf stdout).
  - `device`: alles darüber (DVB-T 7/8 MHz, DVB-C 6,9 MBd …). Der Runner bekommt `output = {"kind":"udp","host":192.168.2.1,"port":1234,"burst":7}` (TS per UDP), der Modulator läuft auf dem Pluto und wird über **dablab-agent** (`pluto_fw/agent/API.md`, Port 8080) gesteuert: `mod/stop` (falls etwas läuft) → `/api/rf` LO + stumm + Bandbreite → `mod/start dvbt_mod -i udp://0.0.0.0:1234 -o iio: … --threads 2 --affinity --rt --window 32 --lo HZ --atten 89.75` (dvbc_mod: `--qam --sr --sps`) mit `cpu=-1`, `rt_prio=50` → Runner `live` → nach dem ersten Status `underflow/clear`. Pegel, Stummschaltung und LO laufen danach über `/api/rf` des Agenten, Underruns = `dac.underflows`, Last/Temperatur/UDP-Fehler in `state.rf.device` und `state.tx`. Stopp = `mod/stop` (der Agent stummt) + Runner beenden. Modulatorende → Fehler und automatischer Neustart wie beim USB-Weg.
  - Läuft auf dem Gerät ein **fremder Modulator** (nicht von diesem Server gestartet), bleibt der Server passiv: USB-Kontext ohne Schreibzugriffe, keine Agent-Schreibbefehle, `/api/rf` speichert nur, `tx/start` → 409 (mit `{"force":true}` wird der fremde Modulator beendet und übernommen). Kontrollempfang (`/api/monitor`) ist im Gerätepfad noch gesperrt.
  - Optionen: `--agent URL|off`, `--device-host`, `--device-udp-port` (1234), `--device-mod-dir` (leer = PATH `/usr/bin`, zum Testen `/tmp`), `--device-rt-prio` (50), `--device-window` (32).

## Medien

- **YouTube** (für DVB-Videoquellen): `POST /api/youtube {url, max_height | quality}` → `{job}`; Runner `media youtube <url> --max-height N` lädt nach `runtime/media/video/`. `GET /api/youtube/<job>` bzw. `state.jobs[job]`: `{status: queued|running|done|error, percent, speed (B/s), eta_s, downloaded_bytes, total_bytes, file, title, duration_s, width, height, error}`. Abbildung der Runner-Werte: `downloading` → `running`, `finished` → `done` (endgültig erst mit dem Ergebnis auf stdout), `speed_bps` → `speed`. `GET /api/jobs` listet alle (die letzten 20 bleiben).
- **Video-Upload**: `POST /api/upload?kind=video` (roh mit `name=` oder multipart) → `runtime/media/video/` (mp4, mkv, ts, m2ts, mpg, mpeg, avi, mov, webm); `/api/media` listet sie als `video/<name>` (Art `video`, mit ffprobe-Auflösung/Codec), `DELETE /api/media/video%2F<name>` löscht.

- `GET /api/media`: Liste mit Dauer, Bitrate, Codec und Tags (Titel/Interpret/Album) aus **ffprobe** (`C:fmpegin` oder `wsl -d dablab -e ffprobe`). Das Sondieren läuft im Hintergrund; bis dahin `probed:false`. Cache `runtime/media_index.json`.
- `DELETE /api/media/<name>`: löscht die Datei und nennt Playlists, die sie verwenden (`playlists`) und ob die Konfiguration sie nennt (`in_config`).

## API-Kurzreferenz

`GET /api/state`, `GET /api/events` (SSE `event: state`, 500 ms), `PUT /api/config`, `POST /api/tx/start|stop`,
`POST /api/rf {channel,freq_hz,offset_hz,lo_offset_hz,atten_db,enabled}`, `POST /api/live` (→ `ctl`), `POST /api/upload?kind=&name=`,
`GET /api/media`, `DELETE /api/media/<name>`, `GET /api/channels`, `GET /api/standards`, `GET /api/dvb/rates`, `POST /api/youtube`, `GET /api/youtube/<job>`, `GET /api/jobs`, `POST /api/check {seconds}`, `POST /api/monitor {seconds,rx_gain_db}`, `GET/PUT/DELETE /api/presets[/name]`,
`POST /api/sweep {from_db,to_db,step_db,dwell_s}`, `POST /api/sweep/stop`. Nach dem Sweep kehrt die Dämpfung auf den Ausgangswert zurück.
