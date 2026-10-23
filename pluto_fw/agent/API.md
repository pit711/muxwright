# dablab-agent – HTTP/JSON-API (Port 8080 auf dem Pluto, 192.168.2.1)

Alle Antworten sind JSON. POST-Bodys sind flache JSON-Objekte. CORS ist offen (`Access-Control-Allow-Origin: *`).
Der Agent öffnet den lokalen IIO-Kontext einmal beim Start; während ein Modulator läuft, teilen sich beide
den `ad9361-phy` (Attribute sind threadsicher im Kernel), der DAC-Puffer gehört aber allein dem Modulator.

Sicherheit: Beim Agentstart, bei jedem Modulatorstart und -ende sowie bei `/api/mod/stop` wird
`hardwaregain = -89,75 dB` gesetzt. `atten_db` wird immer auf 0 … 89,75 begrenzt.

| Methode | Pfad | Body | Antwort / Wirkung |
|---|---|---|---|
| GET | `/api/status` | – | siehe unten |
| POST | `/api/mod/start` | `{"cmd":"dvbt_mod","args":["--udp","5000","--rate","9142857"],"cpu":1,"rt_prio":50,"cwd":"/tmp","atten_db":60}` | startet Programm (PATH: /usr/bin; absolute Pfade wie `/tmp/dvbt_mod` erlaubt) als Kindprozess, Affinität `cpu` (−1 = beide), SCHED_FIFO `rt_prio` (0 = normal, max 90), stdout/stderr → `/tmp/dablab-mod.log`. Vorher: laufenden Modulator beenden, TX stumm, Underflow-Zähler 0, **iiod auf Kern 0 / nice 5**. `atten_db` (optional) wird 300 ms nach dem Start gesetzt. Antwort `{"ok":true,"pid":N,"log":"/tmp/dablab-mod.log"}` |
| POST/GET | `/api/mod/stop` | – | SIGTERM, nach 3 s SIGKILL; TX stumm; iiod zurück auf Kern 1. `{"ok":true,"exit":code}` |
| GET | `/api/mod/log` | – | `{"ok":true,"log":"…letzte 50 Zeilen…"}` |
| POST | `/api/rate` | `{"rate_hz":2048000}` | `ad9361_set_bb_rate()` (libad9361: lädt bei Bedarf einen passenden FIR, erlaubt < 2,083 MS/s) |
| POST | `/api/rate` | `{"rate_hz":9142857,"taps":[…16·n ganzzahlige Koeffizienten, max 128…],"interp":2,"dec":2}` | schreibt `filter_fir_config` (TX GAIN 0 INT `interp`, RX GAIN −6 DEC `dec`, gleiche Taps für RX/TX), schaltet FIR ein und setzt dann die Rate. Antwort enthält `rate_hz` (Ist), `rx_rate_hz`, `fir_en`, `rf_bw_hz` |
| POST | `/api/rate` | `{"fir_en":0}` | FIR ein-/ausschalten ohne Ratenänderung |
| POST | `/api/rf` | `{"lo_hz":185360000,"atten_db":60,"rf_bw_hz":8000000,"rx_lo_hz":…,"rx_rf_bw_hz":…,"rx_gain_db":30,"ensm":"fdd","mute":1}` | jedes Feld optional; Antwort `lo_hz`, `gain_db`, `rf_bw_hz` (Ist) |
| POST | `/api/iiod` | `{"cpu":0,"nice":5}` | iiod (alle Threads) auf Kern `cpu` (−1 = beide) legen, nice setzen |
| POST | `/api/mod/start` | `{"cmd":"pal","file":"pal_k36.iq","lo_hz":593500000,"rate_hz":8000000,"rf_bw_hz":8000000,"atten_db":40,"seconds":0}` | **zyklische IQ-Datei** (PAL-B/G-Schleife aus `mod/tools/pal_bg_gen.py` oder jede andere int16-I/Q-Schleife; `cmd:"cyclic"` ist gleichbedeutend). Wird zu `iqplay -N -c -r RATE -w BW -f DATEI -g ATT [-l LO] [-t s]`: TX-FIR aus, Rate direkt, ganze Datei = ein DMA-Puffer (max. 64 MiB, Größe Vielfaches von 4). `file` ohne `/` = Name in `/tmp/dablab_files`. Dämpfung wird erst nach dem Füllen des Puffers gesetzt (Standard 89,75 = stumm), `seconds` 0/fehlend = bis `/api/mod/stop` |
| POST | `/api/upload?name=pal_k36.iq` | Rohdaten (Content-Length nötig) | speichert nach `/tmp/dablab_files/NAME` (gestreamt, zuerst als `.part`, dann umbenannt). Name `[A-Za-z0-9._-]`, max. 64 Zeichen. Abgelehnt, wenn danach < 16 MiB in /tmp frei blieben. Antwort `{"ok":true,"path":…,"bytes":N}`. Beispiel: `curl -X POST --data-binary @pal_k36.iq "http://192.168.2.1:8080/api/upload?name=pal_k36.iq"` |
| GET | `/api/files` | – | `{"ok":true,"dir":"/tmp/dablab_files","files":[{"name","bytes"}…],"tmp_free":N}` |
| POST | `/api/files/delete` | `{"name":"pal_k36.iq"}` | Datei in `/tmp/dablab_files` löschen |
| POST/GET | `/api/underflow/clear` | – | Underflow-Bit löschen, Zähler 0 |
| POST | `/api/reboot` | `{"mode":"reset"|"ram"|"sf"|"verbose"}` | `device_reboot MODE` (`ram` = U-Boot wartet auf DFU-RAM-Image, `sf` = DFU-Flash-Modus) |

## `/api/status`

```json
{"agent":"0.6","fw":"device-fw v0.31-dablab.20261023","kernel":"4.14.0-dablab-g387d584d434e-dirty",
 "uptime_s":123,"load":[0.10,0.05,0.01],"mem_free_kb":380000,
 "cpu_pct":{"cpu0":3.0,"cpu1":97.5,"all":50.2},"cma_free_kb":200000,"iio_max_block_size":67108864,
 "temp_c":45.2,
 "dac":{"underflows":0,"unf_bit":0,"rate_hz":9142857,"lo_hz":185360000,"gain_db":-89.75,"rf_bw_hz":8000000,
        "fir_en":1,"rx_lo_hz":…,"rx_rate_hz":…,"ensm":"fdd"},
 "mod":{"running":true,"pid":812,"cmd":"dvbt_mod --udp 5000","since_s":42.1,"exit":-1,"cpu":1,"rt_prio":50},
 "iiod":{"pid":806,"cpumask":1},
 "net":{"usb0":{"rx_bytes":…,"rx_pkts":…,"rx_err":0,"rx_drop":0,"tx_bytes":…,"tx_pkts":…}},
 "udp":{"in":…,"noport":…,"in_err":0,"out":…,"rcvbuf_err":0}}
```

- `dac.underflows`: Anzahl 200-ms-Abtastungen, in denen das sticky Underflow-Bit (Register 0x88, Bit 0 des
  `cf-ad9361-dds-core-lpc`) gesetzt war; der Agent liest es alle 200 ms und löscht es (W1C). `unf_bit` = letzter Rohwert.
  Hinweis: Nach dem Anlegen/Starten eines DMA-Puffers ist das Bit konstruktionsbedingt einmal gesetzt – deshalb den
  Zähler nach dem Start des Modulators per `/api/underflow/clear` nullen (bzw. `mod/start` tut das vorher).
- `mod.stats`: letzte vollständige JSON-Zeile aus dem Modulator-Log, unverändert (bei dvbt_mod/dvbc_mod z. B. `t`, `symbols`,
  `rt_factor`, `ts_in`, `stuffed`, `dropped`, `fill`, `clipped`; am Ende die `done`-Zeile). Zusätzlich `mod.clipped` und
  `mod.rt_factor` als eigene Felder, falls vorhanden. Hinweis: `clipped` ist im Modulator derzeit nicht implementiert (immer 0).
- `cpu_pct`: Auslastung je Kern seit der letzten Abtastung (200 ms).
- `udp.rcvbuf_err` / `net.usb0.rx_drop`: steigen, wenn die TS-Zuführung per UDP Pakete verliert.
- `temp_c`: XADC-Chiptemperatur (Zynq).

## Beispiele (PC, PowerShell/curl)

```
curl http://192.168.2.1:8080/api/status
curl -X POST -d "{\"rate_hz\":2048000}" http://192.168.2.1:8080/api/rate
curl -X POST -d "{\"cmd\":\"iqplay\",\"args\":[\"-r\",\"9142857\",\"-z\",\"-t\",\"60\",\"-q\"],\"cpu\":1,\"rt_prio\":50}" http://192.168.2.1:8080/api/mod/start
curl -X POST http://192.168.2.1:8080/api/mod/stop
```

## Modulator-Vertrag (für dvbt_mod/dvbc_mod/fm_mod)

- Programm liest TS/Audio von UDP (Port als Argument), erzeugt IQ und schreibt über den **lokalen** libiio-Kontext
  (`iio_create_local_context()`, Gerät `cf-ad9361-dds-core-lpc`, Kanäle voltage0/voltage1, `iio_buffer_push`).
- Rate selbst setzen (`ad9361_set_bb_rate` oder `filter_fir_config` + `sampling_frequency`), `hardwaregain` beim Start
  auf −89,75 dB, erst nach Pufferfüllung auf den gewünschten Wert.
- Beenden bei SIGTERM, dabei TX stumm.
- Statusausgabe zeilenweise auf stdout (landet in `/tmp/dablab-mod.log`, abrufbar über `/api/mod/log`).
