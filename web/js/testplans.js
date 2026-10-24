// Mitgelieferte Testplan-Vorlagen für typische Empfängerprüfungen.
// Jeder Schritt erzeugt aus der Basiskonfiguration (RF/Ensemble bleiben erhalten) eine neue Konfiguration.

import { newService, hex, capacity, TOTAL_CU } from './dab.js';
import * as STD from './standards.js';
import { T } from './i18n.js';

function svc(n, o = {}) {
  const s = newService(`t${String(n).padStart(2, '0')}`, n);
  s.sid = hex(0xD100 + n, 4);
  s.audio.source = { ...s.audio.source, kind: 'tone', freq_hz: 400 + 100 * (n % 10) };
  Object.assign(s, o.top || {});
  Object.assign(s.audio, o.audio || {});
  if (o.protection) s.protection = o.protection;
  if (o.label) { s.label = o.label; s.shortlabel = o.short || o.label.replace(/\s+/g, '').slice(0, 8); }
  s.pad.dls.text = o.dls || `${s.label}`;
  if (o.mutate) o.mutate(s);
  return s;
}
const base = (b, services, ens = {}) => {
  const c = structuredClone(b);
  c.services = services;
  c.clusters = [];
  Object.assign(c.ensemble, ens);
  return c;
};
const A = l => ({ profile: 'EEP_A', level: l });
const B = l => ({ profile: 'EEP_B', level: l });
const U = l => ({ profile: 'UEP', level: l });
const codecShort = { 'aac-lc': 'LC', 'he-aac': 'HE', 'he-aac-v2': 'HEv2' };

export const TESTPLANS = [
  {
    id: 'codec', title: T('Codec-Matrix'), icon: 'wave',
    desc: T('AAC-LC, HE-AAC und HE-AAC v2 × 32/48 kHz × Mono/Stereo – alle Kombinationen gleichzeitig im Ensemble.'),
    steps: [{
      title: T('Alle Codec-Kombinationen'),
      instruction: T('Jeden Dienst am Prüfling anwählen. Ton (unterschiedliche Frequenz je Dienst), Codec-Anzeige und Kanalzahl kontrollieren.'),
      expect: T('Alle 10 Dienste dekodieren ohne Aussetzer; Mono-Dienste mittig, Stereo-Dienste auf beiden Kanälen.'),
      config: b => {
        const list = []; let n = 1;
        for (const codec of ['aac-lc', 'he-aac', 'he-aac-v2']) for (const sr of [48000, 32000]) for (const ch of [2, 1]) {
          if (codec === 'he-aac-v2' && ch === 1) continue;
          const br = codec === 'aac-lc' ? (ch === 2 ? 96 : 64) : codec === 'he-aac' ? (ch === 2 ? 64 : 48) : 48;
          list.push(svc(n++, { label: `${codecShort[codec]} ${sr / 1000}k ${ch === 2 ? 'Stereo' : 'Mono'}`,
            short: `${codecShort[codec]}${sr / 1000}${ch === 2 ? 'S' : 'M'}`,
            audio: { codec, samplerate: sr, channels: ch, bitrate: br }, protection: A(3) }));
        }
        return base(b, list);
      },
    }],
  },
  {
    id: 'bitrate', title: T('Bitraten & Fehlerschutz'), icon: 'grid',
    desc: T('Bitraten 8–192 kbit/s sowie alle EEP-A/B-Schutzstufen bei 64 kbit/s.'),
    steps: [{
      title: T('Bitraten-Matrix (EEP 3-A)'),
      instruction: T('Alle Dienste nacheinander anwählen, Bitratenanzeige und Klang prüfen.'),
      expect: T('Jeder Dienst dekodiert; angezeigte Bitrate entspricht dem Label.'),
      config: b => base(b, [8, 16, 24, 32, 48, 64, 96, 128, 160, 192].map((br, i) =>
        svc(i + 1, { label: `Rate ${br} kbit/s`, short: `R${br}`, audio: { codec: br <= 48 ? 'he-aac-v2' : br <= 96 ? 'he-aac' : 'aac-lc', bitrate: br, channels: 2 }, protection: A(3) }))),
    }, {
      title: T('Schutz-Matrix (64 kbit/s)'),
      instruction: T('Alle Schutzprofile anwählen; optional mit Pegelrampe die Empfangsschwelle je Profil bestimmen.'),
      expect: T('Alle Dienste dekodieren; stärkerer Schutz (1-A) hält bei geringerem Pegel länger durch.'),
      config: b => base(b, [A(1), A(2), A(3), A(4), B(1), B(2), B(3), B(4)].map((p, i) =>
        svc(i + 1, { label: `Schutz ${p.level}-${p.profile === 'EEP_A' ? 'A' : 'B'}`, short: `EEP${p.level}${p.profile === 'EEP_A' ? 'A' : 'B'}`, audio: { codec: 'he-aac', bitrate: 64 }, protection: p }))),
    }],
  },
  {
    id: 'mp2', title: T('MP2-Dienste (DAB)'), icon: 'radio',
    desc: T('Klassisches DAB mit MPEG Layer II: Stereo, Joint, Dual, Mono, 24 kHz, UEP und EEP.'),
    steps: [{
      title: T('MP2-Mischung'),
      instruction: T('Alle MP2-Dienste anwählen; Dual-Channel-Dienst auf Kanalwahl prüfen.'),
      expect: T('Alle Dienste hörbar, Modus-Anzeige korrekt, 24-kHz-Dienst (LSF) wird unterstützt.'),
      config: b => {
        const mk = (n, label, short, a, p) => svc(n, { label, short, top: { type: 'dab' }, audio: { codec: 'mp2', samplerate: 48000, ...a }, protection: p });
        return base(b, [
          mk(1, 'MP2 128 Stereo', 'MP2-128', { bitrate: 128, channels: 2, mp2_mode: 's' }, U(3)),
          mk(2, 'MP2 192 Joint', 'MP2-192', { bitrate: 192, channels: 2, mp2_mode: 'j' }, U(3)),
          mk(3, 'MP2 96 Mono', 'MP2-96M', { bitrate: 96, channels: 1, mp2_mode: 'm' }, U(3)),
          mk(4, 'MP2 64 24kHz', 'MP2-LSF', { bitrate: 64, samplerate: 24000, channels: 1, mp2_mode: 'm' }, A(3)),
          mk(5, 'MP2 256 Dual', 'MP2-Dual', { bitrate: 256, channels: 2, mp2_mode: 'd' }, U(3)),
        ]);
      },
    }],
  },
  {
    id: 'maxsvc', title: T('Maximale Dienstanzahl'), icon: 'layers',
    desc: T('Volle Ensembles mit vielen kleinen Diensten – Senderliste, Speicher und FIC-Last des Prüflings.'),
    steps: [{
      title: T('24 Dienste à 32 kbit/s (EEP 3-A)'),
      instruction: T('Sendersuchlauf am Prüfling, Anzahl der gefundenen Dienste notieren. Hinweis: Viele Encoder belasten den Erzeuger stark – Testton statt Datei.'),
      expect: T('24 Dienste gefunden, Labels vollständig, alphabetische Sortierung korrekt.'),
      config: b => base(b, Array.from({ length: 24 }, (_, i) => svc(i + 1, { label: `Dienst ${String(i + 1).padStart(2, '0')}`, short: `D${String(i + 1).padStart(2, '0')}`, audio: { codec: 'he-aac-v2', bitrate: 32 }, protection: A(3) }))),
    }, {
      title: T('48 Dienste à 16 kbit/s (EEP 4-A)'),
      instruction: T('Sendersuchlauf wiederholen. Grenzfall für die Senderliste und Labelanzeige.'),
      expect: T('Alle 48 Dienste werden gefunden oder der Prüfling meldet die Grenze sauber.'),
      config: b => base(b, Array.from({ length: 48 }, (_, i) => svc(i + 1, { label: `Grenzfall ${String(i + 1).padStart(2, '0')}`, short: `G${String(i + 1).padStart(2, '0')}`, audio: { codec: 'he-aac-v2', bitrate: 16 }, protection: A(4), mutate: s => { s.audio.source.kind = 'silence'; s.pad.enable = false; } }))),
    }],
  },
  {
    id: 'labels', title: T('Zeichensatz & Labels'), icon: 'type',
    desc: T('Grenzfälle für Labels und DLS: Umlaute, volle 16/8 Zeichen, UTF-8 mit Kyrillisch/Griechisch, lange DLS.'),
    steps: [{
      title: T('EBU-Latin mit Umlauten'),
      instruction: T('Label, Kurzlabel und DLS am Display prüfen (Umlaute, ß, Euro, Sonderzeichen).'),
      expect: T('Alle Zeichen korrekt, kein Ersatzzeichen, Kurzlabel entspricht der Auswahl.'),
      config: b => base(b, [
        svc(1, { label: 'Ärger Öl Übel ß', short: 'ÄrgerÖl', dls: 'Umlaute: ÄÖÜ äöü ß € @ # & – Prüfung' }),
        svc(2, { label: '1234567890ABCDEF', short: '12345678', dls: 'Genau 16 Zeichen Label, 8 Zeichen Kurzlabel' }),
        svc(3, { label: 'A', short: 'A', dls: 'Ein-Zeichen-Label' }),
        svc(4, { label: 'Leer  zeichen  ', short: 'Leer', dls: 'Label mit Doppel- und End-Leerzeichen' }),
      ], { charset: 'ebu', label: 'Grenzfall Ä€', shortlabel: 'GrenzÄ' }),
    }, {
      title: T('UTF-8 (ETSI TS 101 756 Zeichensatz 15)'),
      instruction: T('Darstellung nicht-lateinischer Labels prüfen. UTF-8-Labels haben 16 Byte (nicht Zeichen).'),
      expect: T('Kyrillisch/Griechisch korrekt oder sauberer Fallback ohne Absturz.'),
      config: b => base(b, [
        svc(1, { label: 'Радио 1', short: 'Радио', dls: 'Кириллица: Привет, мир! Проверка DLS.' }),
        svc(2, { label: 'Ελληνικά', short: 'Ελλην', dls: 'Ελληνικό κείμενο για δοκιμή DLS' }),
        svc(3, { label: 'Ünïcødé ✓', short: 'Ünïcødé', dls: 'Gemischt: äöü ✓ ★ ♫ – 128 Zeichen Grenze → ' + 'x'.repeat(80) }),
      ], { charset: 'utf8', label: 'UTF-8 Тест', shortlabel: 'UTF-8' }),
    }],
  },
  {
    id: 'ann', title: T('Durchsagen & Alarm'), icon: 'bell',
    desc: T('ASu/ASw mit Verkehrsfunk-Cluster und Alarm-Cluster 0xFF (AL-Flag gesetzt).'),
    steps: [{
      title: T('Verkehrsfunk und Alarm'),
      instruction: T('Prüfling auf „Musik“ stellen. Im Bereich Durchsagen zuerst Cluster 1 (Verkehr), dann den Alarm-Cluster aktivieren und wieder abschalten.'),
      expect: T('Umschalten auf „Verkehr“-Dienst bei TA ein; Alarm unterbricht immer (auch bei TA aus); Rückkehr nach Ende.'),
      config: b => {
        const c = base(b, [
          svc(1, { label: 'Musik', short: 'Musik', audio: { codec: 'he-aac', bitrate: 72 }, mutate: s => { s.announcements = { supported: ['Traffic', 'Alarm', 'News'], clusters: [1, 255] }; } }),
          svc(2, { label: 'Verkehr', short: 'Verkehr', dls: 'Verkehrsdurchsage aktiv', mutate: s => { s.announcements = { supported: ['Traffic'], clusters: [1] }; s.audio.source.kind = 'lr_test'; } }),
          svc(3, { label: 'Alarm', short: 'Alarm', dls: 'ALARM – Testdurchsage', mutate: s => { s.pty = 31; s.announcements = { supported: ['Alarm'], clusters: [255] }; s.audio.source.freq_hz = 1500; } }),
        ], { alarm_flag: true });
        c.clusters = [
          { id: 1, flags: ['Traffic'], service: c.services[1].key, active: false },
          { id: 255, flags: ['Alarm'], service: c.services[2].key, active: false },
        ];
        return c;
      },
    }],
  },
  {
    id: 'linking', title: T('Linking'), icon: 'link',
    desc: T('Dienstverknüpfung zu UKW (FIG 0/21, PI) und anderen Ensembles (FIG 0/24).'),
    steps: [{
      title: T('UKW-Linking und Ensemble-Verweis'),
      instruction: T('Pegel absenken (Rampe), bis der DAB-Empfang abbricht. Verhalten des Prüflings beobachten (Wechsel auf UKW-PI D3C1 bzw. anderes Ensemble).'),
      expect: T('Prüfling wechselt auf UKW mit passender PI oder meldet die Alternativen korrekt.'),
      config: b => base(b, [
        svc(1, { label: 'Linking UKW', short: 'LinkUKW', mutate: s => { s.linking = { fm_pi: '0xD3C1', fm_freqs_mhz: [87.6, 99.1, 104.4], other_ensembles: [] }; } }),
        svc(2, { label: 'Linking DAB', short: 'LinkDAB', mutate: s => { s.linking = { fm_pi: null, fm_freqs_mhz: [], other_ensembles: [{ eid: '0x10BC', channel: '11D' }] }; } }),
      ]),
    }],
  },
  {
    id: 'sens', title: T('Empfindlichkeitsrampe'), icon: 'ramp',
    desc: T('Ein Referenzdienst, Dämpfungsrampe 40 → 89 dB in 1-dB-Schritten zur Bestimmung der Empfangsschwelle.'),
    steps: [{
      title: T('Referenzdienst + Rampe'),
      instruction: T('Kanal und Kalibrierwert prüfen, Sender einschalten, dann die Rampe starten. Bei Ausfall des Prüflings „Schwelle markieren“ drücken.'),
      expect: T('Schwelle (dBm) notieren; typische Anforderung ≤ −98 dBm (Hersteller-/Normvorgabe beachten).'),
      action: { type: 'sweep', params: { from_db: 40, to_db: 89, step_db: 1, dwell_s: 5 } },
      config: b => base(b, [svc(1, { label: 'Referenz HE 88', short: 'Ref88', audio: { codec: 'he-aac-v2', bitrate: 88 }, protection: A(3), dls: 'Empfindlichkeitsmessung' })]),
    }],
  },
];

export function planCheck(cfg) {
  const std = STD.standardOf(cfg);
  if (std === 'fm') return `${T('UKW')} ${(cfg.rf.freq_hz / 1e6).toFixed(1)} MHz · ±${cfg.fm.deviation_khz} kHz${cfg.fm.rds.enable ? ' · RDS' : ''}${cfg.fm.second_carrier.enable ? ' · ' + T('2. Träger') : ''}`;
  if (std === 'pal') { const c = STD.palCarriers(cfg); return `PAL ${cfg.rf.channel} · ${T('Bild')} ${(c.vision / 1e6).toFixed(2)} MHz · ${cfg.pal.image.kind}`; }
  if (STD.isDvb(std)) { const b = STD.tsBudget(cfg); return b.over ? T('TS überbucht ({used} > {net} Mbit/s)', { used: (b.used / 1e6).toFixed(1), net: (b.net / 1e6).toFixed(1) }) : `${T('{n} Programm(e)', { n: cfg.dvb.programs.length })} · ${(b.used / 1e6).toFixed(1)}/${(b.net / 1e6).toFixed(1)} Mbit/s`; }
  const cap = capacity(cfg);
  return cap.over ? T('Benötigt {used} CU (> {total})', { used: cap.used, total: TOTAL_CU }) : `${T('{n} Dienste', { n: cfg.services.length })} · ${cap.used} CU`;
}

// ---------------------------------------------------------------- UKW / DVB
const withStd = (b, std) => { const c = structuredClone(b); STD.setStandard(c, std); c.clusters = c.clusters || []; return c; };
const fmCfg = (b, mut) => { const c = withStd(b, 'fm'); mut(c.fm, c); return c; };
const prg = (n, o = {}) => { const p = STD.newProgram(`prg${n}`, n); Object.assign(p, o.top || {}); if (o.video) Object.assign(p.video, o.video); if (o.audio) Object.assign(p.audio, o.audio); if (o.eit) Object.assign(p.eit, o.eit); return p; };
const fitPrograms = c => STD.fitPrograms(c);
const dvbCfg = (b, std, mut) => { const c = withStd(b, std); c.dvbs = c.dvbs || STD.defaultDvbs(); mut(c, std === 'dvbt' ? c.dvbt : std === 'dvbs' ? c.dvbs : c.dvbc); return fitPrograms(c); };
/** DVB-S-Feldtest (am TV bestätigt): 11038 MHz V, LOF 9750 → ZF 1288 MHz, 22000 kS/s, QPSK, ONID 0xFF03/TSID 3 */
const dvbsField = (b, fec, programs) => dvbCfgFixed(b, 'dvbs', (c, m) => {
  Object.assign(m, { system: 'dvbs', transponder_mhz: 11038, polarization: 'V', lof_mhz: 9750, tone_22khz: false, symbol_rate: 22000000, fec, modulation: 'QPSK', rolloff: 0.35, pilots: false });
  c.rf.channel = 'sat'; c.rf.freq_hz = STD.satIfHz(m);
  Object.assign(c.dvb.si, { onid: '0xFF03', tsid: '0x0003', network_id: '0xFF03' });
  c.dvb.programs = programs;
});
/** wie dvbCfg, aber Bitraten exakt wie vorgegeben (bestätigte Feldwerte) */
const dvbCfgFixed = (b, std, mut) => { const c = withStd(b, std); c.dvbs = c.dvbs || STD.defaultDvbs(); mut(c, std === 'dvbt' ? c.dvbt : std === 'dvbs' ? c.dvbs : c.dvbc); return c; };

TESTPLANS.push(
  {
    id: 'fm_dev', standard: 'fm', title: T('Hub- & Pilot-Matrix'), icon: 'wave',
    desc: T('Begrenzer, Übermodulation, Pilotpegel und Mono/Stereo-Umschaltung nach IEC 60315-4 / BS.450.'),
    steps: [
      { title: T('Nennhub 100 % (±75 kHz), Pilot 9 %'), instruction: T('1 kHz, Stereo. Audioausgang auf Klirr und Pegel prüfen.'), expect: T('Stereoanzeige, THD ≤ 1 %, kein Begrenzen.'), config: b => fmCfg(b, f => { f.deviation_khz = 75; f.pilot_khz = 6.75; f.stereo = true; }) },
      { title: T('Übermodulation 120 % (±90 kHz), Limiter aus'), instruction: T('Verhalten des Empfängers bei Übermodulation beobachten.'), expect: T('Kein Aussetzer; Verzerrung steigt hörbar, aber kein Mute.'), config: b => fmCfg(b, f => { f.deviation_khz = 90; f.limiter.enable = false; }) },
      { title: T('Hub 30 % (±22,5 kHz)'), instruction: T('Pegelabfall am Audioausgang messen.'), expect: T('≈ −10,5 dB gegenüber 100 %.'), config: b => fmCfg(b, f => { f.deviation_khz = 22.5; }) },
      { title: T('Pilot 4 % (3 kHz)'), instruction: T('Stereo-Einblendschwelle prüfen.'), expect: T('Stereo rastet noch ein (typisch ab 2–3 kHz Pilothub).'), config: b => fmCfg(b, f => { f.pilot_khz = 3; }) },
      { title: T('Pilot 12 % (9 kHz)'), instruction: T('Pilotrest am Audioausgang messen (19 kHz).'), expect: T('Pilotunterdrückung ≥ 40 dB.'), config: b => fmCfg(b, f => { f.pilot_khz = 9; }) },
      { title: T('Mono (kein Pilot)'), instruction: T('Stereoanzeige muss erlöschen, L = R.'), expect: T('Mono-Betrieb, Rauschabstand besser als Stereo.'), config: b => fmCfg(b, f => { f.stereo = false; }) },
    ],
  },
  {
    id: 'fm_rds', standard: 'fm', title: T('RDS-Matrix'), icon: 'text',
    desc: T('PS/RT/RT+, TA-Sprung, AF-Liste, CT, PTYN, EON und RDS-Hub-Grenzen nach EN 50067 / IEC 62106.'),
    steps: [
      { title: T('PS/RT statisch, PTY, CT'), instruction: T('PS „DAB-LAB“, Radiotext und Uhrzeit am Display prüfen (Zeitübernahme nach ≤ 1 min).'), expect: T('PS, RT, PTY korrekt; Uhr stellt sich.'), config: b => fmCfg(b, f => { f.rds.ps = 'DAB-LAB'; f.rds.rt = 'RDS-Test: Radiotext 64 Zeichen – Umlaute äöü ß – Ende'; f.rds.ct = true; }) },
      { title: T('Zeichensatz: Umlaute in PS/RT, Gruppenversion B'), instruction: T('PS „ÄÖÜ-TEST“, RT mit ß/€; 0B/2B-Gruppen.'), expect: T('Zeichen nach Annex E korrekt; Empfänger verarbeitet B-Gruppen.'), config: b => fmCfg(b, f => { f.rds.ps = 'ÄÖÜ-TEST'; f.rds.rt = 'Umlaute äöü ß € – Radiotext Zeichensatz-Test (2B)'; f.rds.group_version = 'B'; f.rds.rt_version = 'B'; }) },
      { title: T('TA-Sprung (TP=1, TA=1)'), instruction: T('Prüfling auf CD/AUX; im Bereich RDS „Durchsage starten“ drücken, später beenden.'), expect: T('Umschaltung auf Radio bei TA, Lautstärkeanhebung, Rückkehr nach TA aus.'), config: b => fmCfg(b, f => { f.rds.tp = true; f.rds.ta = false; }) },
      { title: T('AF-Liste (3 Frequenzen) + zweiter Träger'), instruction: T('Nutzträger per Rampe absenken; AF-Umschaltung auf den zweiten Träger (+200 kHz, gleiches PI) beobachten.'), expect: T('Empfänger wechselt auf die AF ohne PS-Wechsel.'), config: b => fmCfg(b, (f, c) => { f.rds.af = [Math.round((c.rf.freq_hz / 1e6 + 0.2) * 10) / 10, 93.3, 99.9]; f.second_carrier = { ...f.second_carrier, enable: true, offset_khz: 200, level_db: -3, rds: { enable: true, pi: f.rds.pi, ps: f.rds.ps } }; }) },
      { title: T('RT+ und PTYN'), instruction: T('Titel/Interpret-Anzeige (RT+) und PTYN „LABOR“ prüfen.'), expect: T('RT+-Tags werden als Titel/Interpret dargestellt.'), config: b => fmCfg(b, f => { f.rds.rt_from_tags = false; f.rds.rtplus = [{ type: 'ITEM.ARTIST', text: 'DAB-Lab' }, { type: 'ITEM.TITLE', text: 'Prüfsignal 1 kHz' }]; f.rds.rt = 'DAB-Lab - Prüfsignal 1 kHz'; f.rds.ptyn = 'LABOR'; }) },
      { title: T('EON-Eintrag mit TA auf Zweitsender'), instruction: T('EON-Verknüpfung auf den zweiten Träger; dort TA setzen.'), expect: T('Empfänger wechselt zur Durchsage des verknüpften Senders.'), config: b => fmCfg(b, (f, c) => { f.second_carrier = { ...f.second_carrier, enable: true, offset_khz: 300, level_db: -3, rds: { enable: true, pi: '0xD3C5', ps: 'EON-TEST' } }; f.rds.eon = [{ pi: '0xD3C5', ps: 'EON-TEST', pty: 3, af: [Math.round((c.rf.freq_hz / 1e6 + 0.3) * 10) / 10], mapped_af: {}, tp: true, ta: true, pin: null, linkage: 0 }]; }) },
      { title: T('RDS-Hub 1,0 kHz (Minimum)'), instruction: T('BLER im Kontrollempfang und Anzeige am Prüfling.'), expect: T('RDS wird noch dekodiert (Grenze nach EN 50067).'), config: b => fmCfg(b, f => { f.rds.level_khz = 1.0; }) },
      { title: T('RDS-Hub 7,5 kHz (Maximum), Phase 90°'), instruction: T('Audio auf Störungen durch 57 kHz prüfen.'), expect: T('Keine hörbaren Artefakte, RDS fehlerfrei.'), config: b => fmCfg(b, f => { f.rds.level_khz = 7.5; f.rds.phase_deg = 90; }) },
    ],
  },
  {
    id: 'fm_sens', standard: 'fm', title: T('Empfindlichkeit (SINAD)'), icon: 'ramp',
    desc: T('1 kHz mit 40 % Hub (±30 kHz) mono bzw. stereo; Pegelrampe bis zur 26-dB-SINAD- bzw. 46/50-dB-S/N-Schwelle.'),
    steps: [
      { title: T('Mono, ±30 kHz, 1 kHz'), instruction: T('Sender ein, Rampe starten, SINAD am Audioausgang messen, „Schwelle markieren“ bei 26 dB.'), expect: T('Typisch ≤ −103 dBm (≈ 1,6 µV) für 26 dB SINAD.'), action: { type: 'sweep', params: { from_db: 40, to_db: 89, step_db: 1, dwell_s: 5 } }, config: b => fmCfg(b, f => { f.deviation_khz = 30; f.stereo = false; f.rds.enable = false; f.audio.source = { ...f.audio.source, kind: 'tone', freq_hz: 1000, level_dbfs: -0.5 }; }) },
      { title: T('Stereo, ±30 kHz, 1 kHz'), instruction: T('Wie oben in Stereo; Blend-/Mute-Schwelle notieren.'), expect: T('46/50 dB S/N bei deutlich höherem Pegel als mono.'), action: { type: 'sweep', params: { from_db: 30, to_db: 80, step_db: 1, dwell_s: 5 } }, config: b => fmCfg(b, f => { f.deviation_khz = 38.75; f.stereo = true; f.rds.enable = true; f.audio.source = { ...f.audio.source, kind: 'tone', freq_hz: 1000, level_dbfs: -0.5 }; }) },
    ],
  },
  {
    id: 'fm_adj', standard: 'fm', title: T('Nachbarkanal & Fangverhältnis'), icon: 'antenna',
    desc: T('Zweisignalverfahren IEC 60315-4: Störträger bei ±100/±200/±300 kHz (Rauschen, 100 % Hub) sowie Gleichkanal für das Fangverhältnis.'),
    steps: [100, 200, 300].map(k => ({ title: T('Störer +{k} kHz', { k }), instruction: T('Störpegel erhöhen (Pegel des 2. Trägers), bis SINAD des Nutzsignals auf 26 dB fällt.'), expect: T('Selektion ≥ {db} dB (typische Anforderung).', { db: k === 100 ? 6 : k === 200 ? 35 : 55 }), config: b => fmCfg(b, f => { f.second_carrier = { ...f.second_carrier, enable: true, offset_khz: k, level_db: -20, deviation_khz: 75, audio: { source: { ...f.second_carrier.audio.source, kind: 'noise' } } }; }) }))
      .concat([{ title: T('Fangverhältnis (Gleichkanal, −3 dB)'), instruction: T('Zweiter Träger auf 0 kHz Versatz, Pegel 1–3 dB unter dem Nutzträger.'), expect: T('Nutzsignal dominiert (Capture Ratio ≤ 3 dB).'), config: b => fmCfg(b, f => { f.second_carrier = { ...f.second_carrier, enable: true, offset_khz: 0, level_db: -3, audio: { source: { ...f.second_carrier.audio.source, kind: 'tone', freq_hz: 400 } } }; }) }]),
  },
  {
    id: 'dvbt_const', standard: 'dvbt', title: T('Konstellations-/Coderaten-Matrix'), icon: 'grid',
    desc: T('QPSK bis 64-QAM mit Coderaten 1/2 bis 7/8; Videobitrate wird je Schritt an die Nutzbitrate angepasst.'),
    steps: [['QPSK', '1/2'], ['16-QAM', '2/3'], ['64-QAM', '2/3'], ['64-QAM', '7/8']].map(([c, cr]) => ({ title: `${c} ${cr}`, instruction: T('Suchlauf oder manuelle Abstimmung; Bild/Ton und MER/BER im Kontrollempfang prüfen.'), expect: T('Lock und fehlerfreies Bild; Nettorate ≈ {rate} Mbit/s (8 MHz, GI 1/32).', { rate: (STD.dvbtNetRate({ bandwidth_mhz: 8, constellation: c, code_rate: cr, guard: '1/32' }) / 1e6).toFixed(2) }), config: b => dvbCfg(b, 'dvbt', (cfg, m) => { m.constellation = c; m.code_rate = cr; }) })),
  },
  {
    id: 'dvbt_gi', standard: 'dvbt', title: T('Guard-Intervall & Modus'), icon: 'wave',
    desc: T('2k/8k mit GI 1/4 bis 1/32 – TPS-Auswertung und Synchronisation des Receivers.'),
    steps: [['2k', '1/4'], ['2k', '1/32'], ['8k', '1/8'], ['8k', '1/32']].map(([f, g]) => ({ title: `${f}, GI ${g}`, instruction: T('Neustart der Kette abwarten, Receiver neu einlesen lassen.'), expect: T('TPS korrekt erkannt, Lock innerhalb 2 s.'), config: b => dvbCfg(b, 'dvbt', (cfg, m) => { m.fft = f; m.guard = g; }) })),
  },
  {
    id: 'dvbt_lcn', standard: 'dvbt', title: T('LCN & Suchlauf'), icon: 'layers',
    desc: T('Vier Programme (3 TV, 1 Radio) mit LCN 1/5/10/99, EIT und TDT – Senderliste, Sortierung und EPG.'),
    steps: [{ title: T('Suchlauf mit 4 Programmen'), instruction: T('Automatischen Suchlauf am Receiver starten; Senderliste, LCN-Reihenfolge, Jetzt/Danach und Uhrzeit prüfen.'), expect: T('4 Dienste gefunden, Reihenfolge 1/5/10/99 (sofern LCN ausgewertet), EIT-Titel sichtbar.'),
      config: b => dvbCfg(b, 'dvbt', c => { c.dvb.programs = [prg(1, { top: { name: 'DAB-Lab Eins', lcn: 1 } }), prg(2, { top: { name: 'DAB-Lab Zwei HD', lcn: 5 }, video: { codec: 'h264', resolution: 'hd', bitrate_kbps: 6000 }, audio: { codec: 'aac', bitrate_kbps: 128 } }), prg(3, { top: { name: 'DAB-Lab Drei', lcn: 10 } }), prg(4, { top: { name: 'DAB-Lab Radio', lcn: 99, type: 'radio' } })]; c.dvb.si.lcn = true; c.dvb.si.eit = true; c.dvb.si.tdt = true; }) }],
  },
  {
    id: 'dvbt_sens', standard: 'dvbt', title: T('Empfindlichkeit / C/N-Schwelle'), icon: 'ramp',
    desc: T('Ein Programm (64-QAM 2/3), Pegelrampe bis zum Lock-Verlust; QEF-Grenze (BER 2·10⁻⁴ nach Viterbi).'),
    steps: [{ title: T('Rampe 40 → 89 dB'), instruction: T('Sender ein, Rampe starten, „Schwelle markieren“ beim Einfrieren des Bildes.'), expect: T('Typisch ≤ −80 dBm (8 MHz, 64-QAM 2/3).'), action: { type: 'sweep', params: { from_db: 40, to_db: 89, step_db: 1, dwell_s: 5 } }, config: b => dvbCfg(b, 'dvbt', (c, m) => { m.constellation = '64-QAM'; m.code_rate = '2/3'; c.dvb.programs = [prg(1)]; }) }],
  },
  {
    id: 'dvbc_sr', standard: 'dvbc', title: T('Symbolraten & QAM'), icon: 'grid',
    desc: T('6,900 MBd / 256-QAM (Auto-Suchlauf DE), 6,875 MBd / 64-QAM und 3,450 MBd / 256-QAM (USB-Streaming, manuell).'),
    steps: [[6900000, '256-QAM'], [6875000, '64-QAM'], [3450000, '256-QAM']].map(([sr, c]) => ({ title: `${(sr / 1e6).toFixed(3)} MBd ${c}`, instruction: sr > 3600000 ? T('Modulator auf dem Pluto nötig (13,8 MS/s); Auto-Suchlauf am Receiver.') : T('USB-Streaming; Symbolrate am Receiver manuell eingeben.'), expect: T('Lock, MER ≥ 30 dB, Bild/Ton.'), config: b => dvbCfg(b, 'dvbc', (cfg, m) => { m.symbol_rate = sr; m.constellation = c; }) })),
  },
  {
    id: 'dvbc_lcn', standard: 'dvbc', title: T('LCN & Suchlauf (Kabel)'), icon: 'layers',
    desc: T('Vier Programme mit LCN im Hyperband (S21), NIT mit cable_delivery_system_descriptor.'),
    steps: [{ title: T('Suchlauf S21 mit 4 Programmen'), instruction: T('Netzwerksuche am Receiver (NIT-basiert) starten.'), expect: T('Alle Dienste gefunden, Frequenz/Symbolrate aus NIT übernommen.'),
      config: b => dvbCfg(b, 'dvbc', c => { c.rf.channel = 'S21'; c.rf.freq_hz = 306e6; c.dvb.programs = [prg(1, { top: { lcn: 1 } }), prg(2, { top: { name: 'DAB-Lab Zwei HD', lcn: 5 }, video: { codec: 'h264', resolution: 'hd', bitrate_kbps: 8000 }, audio: { codec: 'ac3', bitrate_kbps: 256 } }), prg(3, { top: { lcn: 10 } }), prg(4, { top: { name: 'DAB-Lab Radio', lcn: 99, type: 'radio' } })]; }) }],
  },
  {
    id: 'dvbc_sens', standard: 'dvbc', title: T('Empfindlichkeit (Kabel)'), icon: 'ramp',
    desc: T('Ein Programm 256-QAM, Pegelrampe bis zum Lock-Verlust.'),
    steps: [{ title: T('Rampe 40 → 89 dB'), instruction: T('Rampe starten, Schwelle beim Lock-Verlust markieren.'), expect: T('Typisch ≤ −70 dBm bei 256-QAM.'), action: { type: 'sweep', params: { from_db: 40, to_db: 89, step_db: 1, dwell_s: 5 } }, config: b => dvbCfg(b, 'dvbc', c => { c.dvb.programs = [prg(1)]; }) }],
  },
);

// ---------------------------------------------------------------- PAL & Feldtest
const palCfg = (b, mut) => { const c = withStd(b, 'pal'); mut(c.pal, c); return c; };
const palCh = (c, id) => { const ch = STD.PAL_CHANNELS.find(x => x.id === id); c.rf.channel = id; c.rf.freq_hz = Math.round(ch.mhz * 1e6); return c; };
TESTPLANS.push(
  {
    id: 'pal_basic', standard: 'pal', title: T('Analog-TV: Testbild & Ton'), icon: 'tv',
    desc: T('Farbbalken, Graustufen und Multiburst mit Text, Ton 1 kHz – Suchlauf, Farbdecoder, Tonträger B/G.'),
    steps: [
      { title: T('Farbbalken 75 % + Text, Ton 1 kHz'), instruction: T('Analogen Suchlauf starten; Bild, Farbe, Text und Ton prüfen.'), expect: T('Sender gefunden, Farben korrekt, 1-kHz-Ton.'), config: b => palCfg(b, p => { Object.assign(p.image, { kind: 'testpattern', pattern: 'pal75bars' }); Object.assign(p.sound, { kind: 'tone', freq_hz: 1000 }); }) },
      { title: T('Graustufentreppe'), instruction: T('Linearität und Schwarzwert beurteilen.'), expect: T('8 unterscheidbare Stufen.'), config: b => palCfg(b, p => { p.image.pattern = 'grey'; }) },
      { title: T('PAL-Farbbalken 100 %'), instruction: T('Sättigung und Farbdecoder bei 100 % Farbbalken.'), expect: T('Keine Übersteuerung, Farben satt und korrekt.'), config: b => palCfg(b, p => { p.image.pattern = 'pal100bars'; }) },
      { title: T('Ton aus, Pegel 0,7'), instruction: T('Tonträger aus, Gesamtpegel abgesenkt; Bild und Synchronisation beurteilen.'), expect: T('Bild stabil, kein Ton.'), config: b => palCfg(b, p => { p.sound.kind = 'off'; p.level = 0.7; }) },
    ],
  },
  {
    id: 'field_lg_oled', standard: 'all', title: T('Feldtest TV'), icon: 'target',
    desc: T('Am Fernseher bestätigte Werte: DVB-T K36 594 MHz 8k/16-QAM/2/3/GI 1/4 HD, DVB-C 546 MHz 64-QAM 6900 kBd SD, PAL K36 B/G, DVB-S 11 038 MHz V 22 000 kS/s QPSK 7/8 (3 Dienste) bzw. 1/2.'),
    steps: [
      { title: T('DVB-T K36 594 MHz · 8k 16-QAM 2/3 GI 1/4 · HD'), instruction: T('TV: Antenne → DVB-T → Suchlauf (oder manuell K36, 8 MHz). Dienst „DABLAB Test“ in HD (H.264 1080p25 + AAC), EIT prüfen. Modulator auf dem Pluto (8 MHz).'), expect: T('Dienst „DABLAB Test“ in HD gefunden, Bild und Ton, EIT sichtbar.'),
        config: b => dvbCfgFixed(b, 'dvbt', (c, m) => { c.rf.channel = 'K36'; c.rf.freq_hz = 594e6; Object.assign(m, { bandwidth_mhz: 8, fft: '8k', constellation: '16-QAM', code_rate: '2/3', guard: '1/4' });
          Object.assign(c.dvb.si, { onid: '0xFF01', tsid: '0x0001', network_id: '0xFF01' });
          c.dvb.programs = [prg(1, { top: { name: 'DABLAB Test', provider: 'DABLAB', sid: '0x0001', lcn: 1 }, video: { codec: 'h264', resolution: 'hd', bitrate_kbps: 8000 }, audio: { codec: 'aac', bitrate_kbps: 128 }, eit: { title: 'Feldtest TV', description: 'DVB-T K36 HD 1080p25' } })]; }) },
      { title: T('DVB-C 546 MHz · 64-QAM 6900 kBd · SD'), instruction: T('TV: Kabel → Suchlauf (vollständig oder manuell 546 MHz, 6900 kS/s, 64-QAM). Dienst „DABLAB Kabel“ prüfen.'), expect: T('Dienst „DABLAB Kabel“ in SD (MPEG-2 + MP2) gefunden.'),
        config: b => dvbCfgFixed(b, 'dvbc', (c, m) => { c.rf.channel = 'K30'; c.rf.freq_hz = 546e6; Object.assign(m, { symbol_rate: 6900000, constellation: '64-QAM' });
          Object.assign(c.dvb.si, { onid: '0xFF02', tsid: '0x0002', network_id: '0xFF02' });
          c.dvb.programs = [prg(1, { top: { name: 'DABLAB Kabel', provider: 'DABLAB', sid: '0x0001', lcn: 1 }, video: { codec: 'mpeg2', resolution: 'sd', bitrate_kbps: 5000 }, audio: { codec: 'mp2', bitrate_kbps: 192 } })]; }) },
      { title: T('PAL K36 (Bild 591,25 MHz) B/G'), instruction: T('TV: Analog → Suchlauf oder manuell 591,25 MHz, System B/G. Testbild, Text und Ton prüfen.'), expect: T('Farbbalken mit Text „Feldtest TV“, Ton 1 kHz.'),
        config: b => palCh(palCfg(b, p => { p.system = 'BG'; Object.assign(p.image, { kind: 'testpattern', pattern: 'pal75bars', text: 'DABLAB Test', text2: 'PAL B/G K36' }); Object.assign(p.sound, { kind: 'tone', freq_hz: 1000, deviation_hz: 50000, level_db: -13 }); }), 'K36') },
      { title: T('DVB-S 11 038 MHz V · 22 000 kS/s · QPSK 7/8 · 3 Dienste (HD/UHD/HDR10)'), instruction: T('⚠️ DC-Block zwischen Pluto und Sat-Eingang! LNB-Speisung am Receiver aus. TV: Satellit → manueller Transponder 11 038 MHz V, 22 000 kS/s, LOF 9750 (Low-Band, 22 kHz aus) → Sat-ZF 1288 MHz. Modulator auf dem Pluto.'), expect: T('Drei Dienste: HD (H.264), UHD (HEVC), HDR10 (HEVC Main10) – HDR-Kennung am TV.'),
        config: b => dvbsField(b, '7/8', [
          prg(1, { top: { name: 'DABLAB Sat HD', provider: 'DABLAB', sid: '0x0001', lcn: 1 }, video: { codec: 'h264', resolution: 'hd', bitrate_kbps: 7000, hdr: 'sdr' }, audio: { codec: 'aac', bitrate_kbps: 128 } }),
          prg(2, { top: { name: 'DABLAB Sat UHD', provider: 'DABLAB', sid: '0x0002', lcn: 2 }, video: { codec: 'hevc', resolution: 'uhd', bitrate_kbps: 14000, hdr: 'sdr' }, audio: { codec: 'aac', bitrate_kbps: 128 } }),
          prg(3, { top: { name: 'DABLAB Sat HDR10', provider: 'DABLAB', sid: '0x0003', lcn: 3 }, video: { codec: 'hevc', resolution: 'uhd', bitrate_kbps: 10000, hdr: 'hdr10' }, audio: { codec: 'aac', bitrate_kbps: 128 } }),
        ]) },
      { title: T('DVB-S 11 038 MHz V · 22 000 kS/s · QPSK 1/2 · 1 Dienst'), instruction: T('Wie oben, FEC 1/2 (robuster). Receiver neu abstimmen.'), expect: T('Dienst „DABLAB Sat“ (HD H.264) gefunden.'),
        config: b => dvbsField(b, '1/2', [prg(1, { top: { name: 'DABLAB Sat', provider: 'DABLAB', sid: '0x0001', lcn: 1 }, video: { codec: 'h264', resolution: 'hd', bitrate_kbps: 12000, hdr: 'sdr' }, audio: { codec: 'aac', bitrate_kbps: 128 } })]) },
    ],
  },
  {
    id: 'dvbs_basic', standard: 'dvbs', title: T('DVB-S/S2: Symbolraten & FEC'), icon: 'grid',
    desc: T('DVB-S QPSK 1/2…7/8 bei 6000 kS/s, DVB-S2 QPSK/8PSK mit Rolloff 0,20/0,35 – ⚠️ nur mit DC-Block.'),
    steps: [['dvbs', 'QPSK', '1/2', 0.35], ['dvbs', 'QPSK', '3/4', 0.35], ['dvbs', 'QPSK', '7/8', 0.35], ['dvbs2', 'QPSK', '3/4', 0.2], ['dvbs2', '8PSK', '2/3', 0.2]].map(([sys, mod, fec, ro]) => ({
      title: `${sys === 'dvbs2' ? 'DVB-S2' : 'DVB-S'} ${mod} ${fec}${sys === 'dvbs2' ? ` α ${String(ro).replace('.', ',')}` : ''}`, instruction: T('Receiver neu abstimmen (manueller Transponder), Lock und Bild prüfen.'), expect: T('Lock, Bild/Ton fehlerfrei.'),
      config: b => dvbCfg(b, 'dvbs', (c, m) => { Object.assign(m, { system: sys, modulation: mod, fec, rolloff: ro, pilots: sys === 'dvbs2' }); c.rf.channel = 'sat'; c.rf.freq_hz = STD.satIfHz(m); }) })),
  },
);
