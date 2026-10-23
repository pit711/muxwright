# Stabile Selektoren (`data-testid`)

Selektor: `[data-testid='…']`. Platzhalter in `<…>`. Segment-Schalter erzeugen je Option `<id>-<wert>` (z. B. `svc-type-dab`, `rf-mode-2`, `ens-charset-utf8`, `svc-channels-1`).
Upload-Knöpfe haben ein verstecktes Dateifeld `<id>-input` (für `set_input_files`).

## Mindestumfang (Auftrag)

| Funktion | testid |
|---|---|
| Kanalwahl | `rf-channel` (select, Werte `5A`…`13F`, `LA`…`LW`, `custom`) |
| Dämpfung | `rf-atten` (Schieber), `rf-atten-input` (Zahl, Enter/Change), `rf-atten-step-<±d>` (z. B. `rf-atten-step--10`, `rf-atten-step-0.25`) |
| Senden an/aus | `rf-enable` (Checkbox in der Kopfzeile) |
| Start / Stopp | `tx-start`, `tx-stop` |
| Not-Aus | `estop` |
| DLS-Text / senden | `dls-text`, `dls-send` (Dienste → Reiter `svc-tab-pad`) |
| Dienst hinzufügen | `svc-add` |
| Prüfen | `check-run` |
| Monitor (Kontrollempfang) | `monitor-run` |
| Preset laden | `preset-select` + `preset-load`, oder je Zeile `preset-load-<name>` |
| Durchsage-Schalter | `ann-switch-<clusterId>` (Alarm: `ann-switch-255`) |

## Kopfzeile

`tx-status` (Container, Klasse `off|starting|ready|onair|error`), `tx-state` (Text `AUS`, `STARTET …`, `BEREIT`, `ON AIR`, `FEHLER`),
`hdr-atten`, `hdr-dbm`, `st-pluto`, `st-runner` (Signalerzeugung WSL), `st-underruns`, `st-rate`, `st-buffer`, `st-uptime`,
`conn` (Klasse `conn ok|bad`), `theme-toggle`.

## Navigation & Übernehmen

`nav-hf`, `nav-ensemble`, `nav-dienste`, `nav-medien`, `nav-durchsagen`, `nav-kapazitaet`, `nav-pruefen`, `nav-presets`, `nav-log`, `nav-capacity`.
Hash-Routen: `#/hf`, `#/ensemble`, … (`#main[data-page]` zeigt die aktive Seite).
`applybar`, `apply` (Text „Übernehmen“, „Live übernehmen“ oder „Übernehmen (Neustart)“), `apply-discard`, `apply-errors`, `apply-reload`.

## Dialoge

Jeder Dialog: `<id>`, `<id>-ok`, `<id>-cancel`. IDs: `confirm-atten` (unter 40 dB), `confirm-restart`, `confirm-sweep`, `confirm-del`,
`confirm-monitor`, `confirm-overwrite`, `confirm-preset-del`.

## HF

`card-freq`, `card-level`, `card-mod`, `card-sweep`, `card-cal`; `rf-channel-read`, `rf-chgrid`, `rf-freq` + `rf-freq-apply` (freie Frequenz),
`rf-offset`, `rf-lo-offset`, `rf-samplerate`, `rf-atten-read`, `rf-dbm-read`, `rf-backoff`, `rf-cfr`, `rf-mode` (`rf-mode-1`…`-4`),
`tii-enable`, `tii-comb`, `tii-pattern`, `tii-old`.
Pegelassistent: `sweep-from`, `sweep-to`, `sweep-step`, `sweep-dwell`, `sweep-start`, `sweep-stop`, `sweep-progress`, `sweep-status`, `sweep-mark`, `sweep-marks`.
Kalibrierung: `cal-table`, `cal-channel`, `cal-value`, `cal-add`.

## Ensemble

`ens-eid`, `ens-ecc`, `ens-country`, `ens-charset`, `ens-label`, `ens-shortlabel`, `ens-shortchips` (Knöpfe je Zeichen), `ens-lto`,
`ens-datetime`, `ens-alarm`, `ens-inttable`, `ens-rc-mode`, `ens-rc`.

## Dienste

Liste: `svc-list`, `svc-item-<index>`, `cap-mini`, `svc-add`. Editor: `svc-editor`, `svc-dup`, `svc-del`,
Reiter `svc-tab-general|audio|source|pad|sls|ann|link`.
- Allgemein: `svc-sid`, `svc-key`, `svc-ecc`, `svc-type` (`svc-type-dabplus|dab|data`), `svc-label`, `svc-shortlabel`, `svc-shortchips`, `svc-pty`, `svc-lang`, `svc-ptydyn`
- Audio: `svc-codec`, `svc-samplerate`, `svc-channels`, `svc-bitrate`, `svc-mp2mode`, `svc-gain`, `svc-bw`, `svc-protection` (Werte `EEP_A:3`, `EEP_B:2`, `UEP:3` …)
- Daten: `svc-data-kind`, `svc-data-bitrate`, `svc-data-addr`, `svc-data-files`
- Quelle: `card-source`, `src-kind-<file|playlist|tone|noise|pinknoise|sweep|lr_test|polarity|silence>`, `src-file`, `src-upload`, `src-loop`, `src-level`, `src-freq`, `src-left`, `src-right`, `src-sfrom`, `src-sto`, `src-sdur`
- DLS: `card-dls`, `pad-enable`, `pad-bytes`, `dls-enable`, `dls-charset`, `dls-text`, `dls-send`, `dls-rmlabel`, `dlplus-enable`, `dlplus-artist|title|album|station|program|url`, `dls-rotate`, `dls-rotate-s`
- Slideshow/Logo: `card-sls`, `sls-enable`, `sls-upload`, `sls-pick`, `sls-thumbs`, `sls-send-<i>`, `sls-del-<i>`, `sls-interval`, `sls-maxbytes`, `sls-raw`, `sls-cat`, `logo-upload`
- Durchsagen: `asu`, `asu-<Typ>` (z. B. `asu-Traffic`, `asu-Alarm`), `asu-cluster-<id>`
- Linking: `link-pi`, `link-freqs`, `link-oe-add`, `link-oe-eid-<i>`

### Quelle „Playlist“ (Dienste → Quelle)

`src-kind-playlist`, `src-playlist` (select, Playlist-Name), `src-playlist-edit` (Link zum Bereich Medien), `src-shuffle`, `src-loop`,
`src-normalize`, `src-crossfade`, `src-dls-tags`. Jetzt läuft: `np-box`, `np-title`, `np-prev`, `np-next`.
Kopfzeile kompakt: `hdr-np` (nur sichtbar, wenn ein Playlist-Dienst spielt), `hdr-np-title`, `hdr-np-prev`, `hdr-np-next`.

## Medien & Playlists

Navigation `nav-medien`, Route `#/medien`.
- Bibliothek: `card-media`, `media-upload` (+ `media-upload-input`, mehrere Dateien), `media-refresh`, `media-list`, `media-row-<i>`, `pl-add-<i>` (Datei i zur gewählten Playlist), `media-del-<i>` (+ Dialog `confirm-media-del`).
- Playlists: `card-playlists`, `pl-select`, `pl-new`, `pl-rename` (Namensdialog `pl-name`, Feld `pl-name-input`, `pl-name-ok`/`-cancel`), `pl-del` (+ `confirm-pl-del`),
  `pl-import` (+ `pl-import-input`, .m3u/.m3u8/.pls; ohne gewählte Playlist wird ein Name abgefragt), `pl-export` (Download .m3u8), `pl-save`.
- Titel: `pl-items`, `pl-item-<i>` (draggable; Klasse `playing` beim laufenden Titel), `pl-up-<i>`, `pl-down-<i>`, `pl-remove-<i>`.
- Jetzt läuft je Playlist-Dienst: `np-<key>` (Box), `np-<key>-title`, `np-<key>-prev`, `np-<key>-next`.

## Durchsagen (ASw)

`ann-live`, `cluster-add`, `cluster-add-alarm`, `cluster-<id>` (Karte, Klasse `active` wenn aktiv), `ann-switch-<id>`, `cluster-id-<id>`, `cluster-svc-<id>`, `cluster-del-<id>`.

## Kapazität

`card-capacity`, `cap-used`, `cap-bar`, `cap-over` (nur bei Überbuchung), `cap-table`.

## Prüfen

`check-seconds`, `check-run`, `monitor-seconds`, `monitor-gain`, `monitor-run`, `report-tab-check`, `report-tab-monitor`,
`report-check` / `report-monitor`, `report-<kind>-verdict`, `report-<kind>-ensemble`, `report-<kind>-svc-<key>`. Abweichende Zeilen: `tr.bad`.

## Presets, Testpläne, Protokoll

`card-presets`, `preset-select`, `preset-load`, `preset-list`, `preset-row-<name>`, `preset-load-<name>`, `preset-del-<name>`,
`preset-name`, `preset-save`, `config-export`, `config-import` (+ `preset-import-input`).
`card-plans`, `plan-list`, `plan-<codec|bitrate|mp2|maxsvc|labels|ann|linking|sens>`, `card-plan`, `plan-close`, `plan-steps`,
`step-<i>` (Klasse `pass|fail|skip`), `step-load-<i>`, `step-pass-<i>`, `step-fail-<i>`, `step-note-<i>`.
`card-protocol`, `protocol-notes`, `protocol-json` (Download), `protocol-html` (öffnet neues Fenster).

## Log

`log-filter`, `log-lvl-info|warn|error`, `log-follow`, `log-clear`, `log-export`, `log-list` (Zeilen `.logline.<lvl>`).

## Mehrstandard (DAB+ | UKW | DVB-T | DVB-C)

Kopfzeile: `std-switch` mit `std-dab`, `std-fm`, `std-dvbt`, `std-dvbc` (Entwurf; Wechsel = „Übernehmen (Neustart)“), `hdr-std` (aktiver Standard laut Server), `hdr-std-info`.
Navigation je Standard: UKW `nav-sender`, `nav-rds`; DVB `nav-modulation`, `nav-programme`, `nav-si`; `nav-std-label`.
HF (alle): `card-path`, `rf-path`, `rf-path-actual`, `rf-path-required`, `rf-path-warn`. UKW: `fm-freq` (MHz, Enter/Change), `fm-freq-apply`, `fm-freq-step-<±d>`, `fm-presets`. DVB: `dvb-channel` (select), `dvb-chgrid`, `rf-freq`/`rf-freq-apply` (frei).

### UKW – Sender (`#/sender`)
`card-fm-mpx`, `fm-deviation`, `fm-pilot`, `fm-mpx-power`, `fm-preemph` (`-50|-75|-0`), `fm-stereo` (`-true|-false`), `fm-limiter`, `fm-audio-bw`, `fm-total-dev`, `fm-gain`,
Audioquelle wie bei DAB mit Präfix `src-…` (`card-fm-source`), zweiter Träger: `card-fm-second`, `fm-second-enable`, `fm-second-offset`, `fm-second-level`, `fm-second-dev`, Quelle mit Präfix `src2-…` (`src2-kind-<art>`), `fm-second-rds`, `fm-second-pi`, `fm-second-ps`.

### UKW – RDS (`#/rds`)
`card-rds-ident`: `rds-pi`, `rds-ecc`, `rds-enable`, `rds-pty`, `rds-ptyn`, `rds-level`, `rds-phase`. Flags: `rds-tp`, `rds-ta`, `rds-ms`, `rds-di-stereo`, `rds-ct`, `rds-ta-live` (Durchsage ein/aus, live).
Texte: `rds-ps`, `rds-ps-send` (live), `rds-ps-rotate`, `rds-ps-rotate-s`, `rds-rt`, `rds-rt-send` (live), `rds-rt-ab`, `rtplus-enable`, `rtplus-auto`, `rtplus-<artist|title|album|station|program|url>`, `rds-lps`.
AF: `rds-af-list` (Chips), `rds-af-input` (Enter), `rds-af-add`, `rds-af-self`, `rds-af-del-<i>`. EON: `rds-eon-table`, `rds-eon-add`, `eon-pi-<i>`, `eon-ps-<i>`, `eon-ta-<i>`.

### DVB – Modulation (`#/modulation`)
`dvb-netrate`, `dvb-sps`; DVB-T: `card-dvbt`, `dvbt-bandwidth-<5|6|7|8>`, `dvbt-fft-<2k|8k>`, `dvbt-constellation-<QPSK|16-QAM|64-QAM>`, `dvbt-coderate-<1/2…7/8>`, `dvbt-guard-<1/4…1/32>`, `dvbt-cellid`, `dvbt-hierarchy`, `dvbt-ratetable`;
DVB-C: `card-dvbc`, `dvbc-symbolrate` (kBd), `dvbc-sr-<6875000|6900000|3450000>`, `dvbc-constellation-<16…256-QAM>`, `dvbc-ratetable`.

### DVB – Programme (`#/programme`)
`card-ts`, `ts-bar`, `ts-bar-mini`, `ts-over` (nur bei Überbuchung), `nav-ts` (Navigation). Liste `prg-list`, `prg-item-<i>`, `prg-add`, Editor `prg-editor`, `prg-dup`, `prg-del` (+ `confirm-prg-del`), Reiter `prg-tab-<general|video|audio|eit>`.
Allgemein: `prg-name`, `prg-provider`, `prg-sid`, `prg-lcn`, `prg-key`, `prg-type-<tv|radio>`.
Video: `vid-kind-<testpattern|file|youtube|playlist>`, `vid-pattern`, `vid-file`, `vid-upload` (+ `-input`), `vid-loop`, `vid-yt-url`, `vid-yt-start`, `vid-yt-progress`, `vid-yt-status`, `vid-playlist`, `vid-codec-<mpeg2|h264>`, `vid-res`, `vid-bitrate`, `vid-bitrate-preset`.
Audio: `aud-codec-<mp2|aac|ac3>`, `aud-bitrate`, Quelle mit Präfix `aud-…` (`aud-kind-<art>`, `card-prg-audio`). EIT: `eit-title`, `eit-desc`, `eit-duration`.

### DVB – SI-Tabellen (`#/si`)
`card-si`, `si-network-id`, `si-network-name`, `si-onid`, `si-tsid`, `si-lcn`, `si-eit`, `si-tdt`, `si-services`, `card-si-delivery`.

### Berichte UKW/DVB (Prüfen)
UKW: `report-<check|monitor>`, `report-verdict`, `report-<kind>-mpx`, `report-<kind>-rds`. DVB: `report-<kind>-mod`, `report-<kind>-si`, `report-<kind>-prg-<key>`. Abweichungen: `tr.bad`. Monitor zusätzlich `report-monitor-rx`, `report-monitor-spectrum`, `report-monitor-clip`.

### Testpläne
UKW: `plan-fm_dev`, `plan-fm_rds`, `plan-fm_sens`, `plan-fm_adj`; DVB-T: `plan-dvbt_const`, `plan-dvbt_gi`, `plan-dvbt_lcn`, `plan-dvbt_sens`; DVB-C: `plan-dvbc_sr`, `plan-dvbc_lcn`, `plan-dvbc_sens` (nur im jeweils gewählten Standard sichtbar).

## Berichtformate und API-Annahmen für UKW/DVB (bis SPEC.md sie festlegt)

- Konfiguration: `mode` = `1..4` (DAB) oder `"fm" | "dvbt" | "dvbc"`; Parameter in `fm`, `dvbt`, `dvbc`, `dvb` (Programme + SI); HF-Träger weiterhin in `rf` (`rf.channel` = `"fm"` | DVB-Kanal `K21`/`E5`/`S21` | `custom`).
- Live: `POST /api/live {"rds": {"ps"|"rt"|"rt_ab"|"ta"|"rtplus": …}}`; EIT-Texte live (`dvb.programs[i].eit.*`).
- YouTube: `POST /api/youtube {url, quality}` → `{job}`; `GET /api/youtube/<job>` → `{status: queued|running|done|error, percent, speed, eta_s, file, title, error}`; zusätzlich `state.jobs[job]`.
- Videoupload: `POST /api/upload?kind=video` (Raw oder Multipart) → `media/video/…`.
- Zustand: `state.rf.path` = `"usb" | "pluto"` (Ausgabeweg), `state.jobs`.
- Berichte: siehe Kopf von `web/js/report_std.js` (UKW: Hub/Pilot/Phase/MPX/Stereotrennung + `rds{…}` aus redsea; DVB: Lock/MER/BER/Rate/PCR + `tables`, `nit`, `lcn`, `services[]`, `ffprobe`). Monitor-Berichte weiterhin mit äußerem Mitschnitt (`spectrum`, `decode`).

## Analog-TV PAL (`mode: "pal"`, Bereich `#/pal`)

Umschalter `std-pal`, Navigation `nav-pal`. HF: `dvb-channel` (Werte `E2`–`E12`, `K21`–`K69`, Bildträger = Kanalgrenze + 1,25 MHz), `pal-carriers`.
Bild: `card-pal-video`, `pal-preview` (SVG-Vorschau), `pal-kind-<testpattern|image|file|youtube>`, `pal-pattern`, `pal-image` (+ `pal-image-upload`), `pal-file` (+ `pal-upload`),
YouTube wie DVB (`vid-yt-url`, `vid-yt-start`, `vid-yt-status`), `pal-loop-hint` (Analog-Loop 160 ms), `pal-text-top`, `pal-text-bottom`, `pal-text-send` (live), `pal-video-level`, `pal-residual`.
System/Ton: `card-pal-system`, `pal-system-<BG|I|DK>`, `pal-system-carriers`, `card-pal-audio`, `pal-audio-kind-<tone|file|off>`, `pal-tone-freq`, `pal-tone-level`, `pal-audio-file`, `pal-audio-dev`, `pal-sound-level`, `pal-audio-preemph-<50|0>`.
Bericht: `report-<kind>-pal` (Soll/Ist), `report-<kind>-levels`, `report-<kind>-image` (PNG aus `image`). Testpläne: `plan-pal_basic`, `plan-field_lg_oled` (in allen Standards sichtbar; Schritte wechseln den Standard).

Annahmen PAL (bis SPEC.md `pal` festlegt): `pal{system:"BG"|"I"|"DK", sample_rate, live, video{source{kind,pattern,image,file,url,loop}, text_top, text_bottom, level_db, residual_carrier_pct},
audio{source{kind:"tone"|"file"|"off", freq_hz, level_dbfs, file}, deviation_khz, sound_level_db, preemphasis_us}}`, `rf.freq_hz` = Bildträger. Live: `POST /api/live {"pal":{"text_top","text_bottom"}}`.
Bericht: `{ok, lock, vision_carrier_hz, sound_carrier_hz, sound_offset_hz, sound_deviation_khz, vision_sound_ratio_db, levels{sync_tip_pct,blanking_pct,black_pct,white_pct,residual_carrier_pct},
line_freq_hz, color_burst, snr_db, image:"data:image/png;base64,…"|Pfad, image_info, [fields[{name,soll,ist,ok}]]}`.
Ausgabeweg: `state.rf.path` = `"usb"|"device"` (auch `"pluto"`), optional `path_label` `"usb"|"pluto"`; `state.rf.pal_live` = Server spielt PAL live (statt 160-ms-Loop).
YouTube: `state.jobs[job].status` `running|done|error` (auch `downloading|finished`), `speed` in Byte/s oder Text.

## DVB-S / DVB-S2 (`mode: "dvbs"`, Bereiche wie DVB: `#/modulation`, `#/programme`, `#/si`)

Umschalter `std-dvbs` (zeigt beim Wechsel die 13/18-V-Warnung als Toast). Warnhinweis `dvbs-lnb-warning` (Modulation und HF).
Transponder/LNB: `card-dvbs-lnb`, `dvbs-transponder` (MHz), `dvbs-lof`, `dvbs-lof-preset` (9750/10600/5150/10750/11300), `dvbs-pol-<H|V>`, `dvbs-22khz`, `dvbs-if` (Sat-ZF), `dvbs-transponder-read`.
Modulation: `card-dvbs`, `dvbs-system-<dvbs|dvbs2>`, `dvbs-symbolrate` (kS/s), `dvbs-sr-<6000000|27500000|22000000|3000000>`, `dvbs-modulation-<QPSK|8PSK>`, `dvbs-fec`, `dvbs-rolloff-<0.35|0.25|0.2>`, `dvbs-pilots`.
HF: `dvbs-hf-info`. Testpläne: `plan-dvbs_basic`; Feldtest `plan-field_lg_oled` Schritt 4 (`step-load-3`).

Annahmen DVB-S (bis SPEC.md `dvbs` festlegt): `dvbs{system:"dvbs"|"dvbs2", transponder_mhz, polarization:"H"|"V", lof_mhz, tone_22khz, symbol_rate, fec, modulation:"QPSK"|"8PSK", rolloff:0.35|0.25|0.2, pilots}`,
`rf.channel = "sat"`, `rf.freq_hz` = Sat-ZF = (Transponder − LOF) · 1e6 (950–2150 MHz). Nutzbitrate DVB-S = SR·2·FEC·188/204, DVB-S2 ≈ SR·bits·FEC·(0,9636 mit / 0,9888 ohne Pilots)·0,993 (Näherung).
Bericht wie DVB, `modulation{system, transponder_mhz, polarization, symbol_rate, modulation_type, fec, roll_off, frequency_hz}`; NIT mit satellite_delivery_system_descriptor (0x43).

### Feldtest LG OLED (bestätigte Werte)
DVB-T K36 594 MHz, 8 MHz, 8k/16-QAM/2/3/GI 1/4, „DABLAB Test“ (ONID 0xFF01, TSID 0x0001, SID 0x0001), H.264 1080p25 + AAC ·
DVB-C 546 MHz (`rf.channel` K30), 64-QAM, 6900 kBd, „DABLAB Kabel“ (ONID 0xFF02, TSID 0x0002, SID 0x0001), MPEG-2 SD + MP2 ·
PAL K36 B/G (Bild 591,25 MHz) · DVB-S 11 300 MHz H, LOF 9750 (22 kHz aus) → ZF 1550 MHz, 6000 kS/s, QPSK 3/4, „DABLAB Sat“ (ONID 0xFF03, TSID 0x0003).
