// Danke: Projekte und Menschen, auf deren Arbeit Muxwright aufbaut (Einrichtung „Willkommen“ und „Fertig“).
import { h, icon } from './ui.js';
import { CREDITS_T as T } from './credits_i18n.js';

// [Gruppe, Name, Wer, Wofür, Link]
export const CREDITS = [
  ['ref', 'PlutoDVB2, libdvbmod, dvbsdr, pluto-ori-ps', 'Evariste Courjaud, F5OEO', 'DATV on the PlutoSDR, on-device modulation, firmware approach', 'https://github.com/F5OEO'],
  ['ref', 'DATV-Express / DVB-S modulator', 'Charles Brain, G4GUO', 'origin of libdvbmod', 'https://github.com/F5OEO/libdvbmod'],
  ['ref', 'Portsdown & DATV wiki', 'British Amateur Television Club (BATC)', 'DATV practice, Pluto firmware notes', 'https://wiki.batc.org.uk/Portsdown_4_Pluto'],
  ['ref', 'QO-100 band plan', 'AMSAT-DL', 'Es’hail-2 / QO-100 wideband transponder', 'https://amsat-dl.org/'],
  ['ref', 'dtv-utils', 'Ron Economos, W6RZ', 'DVB-T rates, test streams', 'https://github.com/drmpeg/dtv-utils'],
  ['ref', 'PySDR', 'Marc Lichtman', 'FM/RDS demodulation (textbook)', 'https://pysdr.org/'],
  ['code', 'PiFmRds', 'Christophe Jacquet, F8FTK', 'RDS CRC/offset words, groups 0A/2A/4A, biphase shaping', 'https://github.com/ChristopheJacquet/PiFmRds'],
  ['code', 'PiFmAdv', 'Miegl', 'RDS 0A header, AF method A', 'https://github.com/miegl/PiFmAdv'],
  ['code', 'MiniRDS', 'Anthony96922', 'RDS 1A/3A/10A/11A RT+/15A, repetition rates', 'https://github.com/Anthony96922/MiniRDS'],
  ['code', 'gr-rds', 'Bastian Bloessl', 'RDS levels, syndromes', 'https://github.com/bastibl/gr-rds'],
  ['base', 'plutosdr-fw, libiio, Linux, HDL', 'Analog Devices', 'ADALM-Pluto firmware base, IIO', 'https://github.com/analogdevicesinc'],
  ['base', 'Buildroot', 'Buildroot project', 'Pluto root filesystem', 'https://buildroot.org/'],
  ['base', 'Bootlin toolchains', 'Bootlin', 'ARM cross compiler', 'https://toolchains.bootlin.com/'],
  ['base', 'ODR-mmbTools', 'Opendigitalradio (Matthias P. Brändli et al.)', 'DAB+ audio encoder, PAD, multiplexer, modulator', 'https://github.com/Opendigitalradio'],
  ['base', 'FDK-AAC', 'Fraunhofer IIS', 'HE-AAC encoder (installed by the user)', 'https://github.com/mstorsjo/fdk-aac'],
  ['base', 'FFmpeg, x264, x265', 'FFmpeg / VideoLAN / MulticoreWare', 'video and audio encoding (installed by the user)', 'https://ffmpeg.org/'],
  ['base', 'FFmpeg Windows builds', 'BtbN', 'NVENC build for Windows', 'https://github.com/BtbN/FFmpeg-Builds'],
  ['base', 'TSDuck', 'Thierry Lelégard', 'MPEG-TS, NIT/SDT/EIT', 'https://tsduck.io/'],
  ['base', 'dfu-util', 'dfu-util developers', 'firmware transfer over USB', 'https://dfu-util.sourceforge.net/'],
  ['base', 'NumPy, SciPy, paramiko, Python', 'their communities', 'signal processing, SSH, runtime', 'https://numpy.org/'],
  ['base', 'yt-dlp', 'yt-dlp contributors', 'optional video sources', 'https://github.com/yt-dlp/yt-dlp'],
  ['check', 'welle.io / welle-cli', 'Albrecht Lohöfener', 'DAB monitoring receiver', 'https://github.com/AlbrechtL/welle.io'],
  ['check', 'redsea', 'Oona Räisänen, OH2EIQ', 'RDS decoder for verification', 'https://github.com/windytan/redsea'],
  ['check', 'GNU Radio gr-dtv', 'Ron Economos, W6RZ, and the GNU Radio project', 'DVB-T/S2 and ATSC reference for bit-exact tests', 'https://github.com/gnuradio/gnuradio'],
  ['check', 'gr-isdbt', 'ARTES group (Universidad de la República, Uruguay)', 'independent ISDB-T receiver for loopback tests', 'https://github.com/git-artes/gr-isdbt'],
  ['base', 'flag-icons', 'Panayiotis Lipiridis', 'country flags per standard (MIT)', 'https://github.com/lipis/flag-icons'],
];

export function creditsPanel(lang, { open = false } = {}) {
  const t = T[lang] || T.en;
  const groups = ['ref', 'code', 'base', 'check'];
  return h('details.onb-credits', { tid: 'onb-credits', open: open || null },
    h('summary', null, icon('star'), ' ', t.h),
    h('p.small.muted', null, t.intro),
    ...groups.map(g => h('div', null, h('h4', null, t.groups[g]),
      h('ul.credits', null, CREDITS.filter(c => c[0] === g).map(([, name, who, what, url]) =>
        h('li', { tid: 'credit' }, h('a', { href: url, target: '_blank', rel: 'noopener' }, name), ' – ', h('b', null, who),
          h('span.muted', null, ` · ${what}`)))))));
}
