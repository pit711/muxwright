# Lizenzhinweise – `runner/fm/`

Der UKW-Teil (`rds.py`, `dsp.py`, `fm_tx.py`, `fm_check.py`) ist eine eigene Python-Umsetzung. Teile sind nach
folgenden Vorlagen entstanden (Algorithmen und einzelne Codestellen, insbesondere CRC/Offsetwörter, Gruppenaufbau,
RT+-Bitaufteilung, AF-Codierung, Wiederholraten, Biphase-Formung). Alle genannten Vorlagen stehen unter der
**GNU General Public License v3.0**; bei Weitergabe dieses Werkzeugs sind die Quellen offenzulegen und die GPL-3.0
zu beachten (intern unproblematisch).

| Vorlage | Autor / Copyright | Lizenz | Übernommen |
|---|---|---|---|
| [PiFmRds](https://github.com/ChristopheJacquet/PiFmRds) `src/rds.c`, `src/fm_mpx.c`, `src/generate_waveforms.py` | © 2014 Christophe Jacquet, F8FTK | GPL-3.0 | CRC-10 (`POLY 0x1B9`), Offsetwörter A–D, Bitausgabe 16+10 Bit, Gruppen 0A/2A/4A (MJD-Formel), 57-kHz-Trick (0,+1,0,−1 bei 228 kHz), Pilot-/38-kHz-Tabellen, Impulspaar + Formfilter |
| [PiFmAdv](https://github.com/miegl/PiFmAdv) `src/rds.c` | © 2017 Miegl | GPL-3.0 | Gruppenkopf 0A mit TP/PTY/TA/MS/DI, AF-Liste Methode A (224+n, Füller 0xCD) |
| [MiniRDS](https://github.com/Anthony96922/MiniRDS) `src/rds.c`, `src/lib.c` | © 2019 Anthony96922 | GPL-3.0 | Offsetwort C′ (0x350) für Typ-B-Gruppen, 1A (ECC/LIC), 3A (ODA-Anmeldung), 10A (PTYN), 11A RT+ (Bitaufteilung, Item-Toggle/Running), 15A LPS, Prioritäten/Wiederholraten (1A/60, 3A/20, 10A/10, 11A/30), RT-Abschluss mit `\r` und A/B-Kipp |
| [gr-rds](https://github.com/bastibl/gr-rds) | © Bastian Bloessl u. a. | GPL-3.0 | Pegelreferenz des TX-Flowgraphs (Pilot 0,1, RDS 2000/75e3), Syndrome/Offsetwörter dezimal (Selbsttest) |
| [redsea](https://github.com/windytan/redsea) | © Oona Räisänen, OH2EIQ | MIT | wird als externes Programm zur Verifikation aufgerufen (nicht eingebettet); in WSL aus den Quellen gebaut (`/opt/dablab/src/redsea`, Binary `/opt/dablab/bin/redsea`) |
| [PySDR, Kapitel RDS](https://pysdr.org/content/rds.html) | Marc Lichtman | CC BY-NC-SA 4.0 (Lehrtext) | Idee des Quadraturdemodulators `angle(x[1:]·conj(x[:-1]))` für die Prüfkette |

Normbezüge: EN 50067:1998 (RDS; Zeichensatz Annex E, Blockcode Annex A/B, MJD Annex G, Gruppen 3.1.5.x),
IEC 62106 (RDS/RDS2, LPS 15A), ITU-R BS.450 (Pilotton-Stereo, Preemphasis), ITU-R BS.412 (MPX-Leistung),
IEC 60315-4 (Messverfahren UKW-Empfänger). Normtexte sind nicht Teil dieses Repositories.

Die FIR-Preemphasis, der zustandsbehaftete Polyphasen-Resampler, der Limiter, die MPX-Leistungsregelung, der
EON-Encoder (14A/14B), die CT-Minutenkantenplanung und die gesamte Prüfkette (`fm_check.py`) sind Eigenentwicklungen.
