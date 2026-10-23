// Prüft die Dashboard-Übersetzungen: jeder T('…')-Text in web/ muss in allen 6 Sprachen eines Wörterbuchs stehen.
//   node test/i18n_check.mjs            (alle Dateien)
//   node test/i18n_check.mjs a          (nur Wörterbuch a.js und die Dateien, die es abdeckt)
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'web');
const AREAS = {
  a: ['app.js', 'index.html', 'js/core.js', 'js/regie.js', 'js/hf_std.js', 'js/ui.js', 'js/media.js', 'js/standards.js', 'js/settings.js'],
  b: ['js/pages.js', 'js/dab.js'],
  c: ['js/dvb.js', 'js/fm.js', 'js/pal.js', 'js/report.js', 'js/report_std.js', 'js/testplans.js'],
};
const LANGS = ['en', 'fr', 'es', 'it', 'nl', 'pl'];
const only = process.argv[2];

function keys(file) {
  const src = fs.readFileSync(path.join(WEB, file), 'utf8');
  const out = new Set();
  const rx = /\bT\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`((?:[^`\\$]|\\.)*)`)/g;
  for (const m of src.matchAll(rx)) out.add((m[1] ?? m[2] ?? m[3]).replace(/\\(['"`\\])/g, '$1').replace(/\\n/g, '\n'));
  for (const m of src.matchAll(/data-i18n(?:-title)?="([^"]*)"/g)) out.add(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
  return out;
}

let bad = 0, total = 0;
for (const [area, files] of Object.entries(AREAS)) {
  if (only && only !== area) continue;
  const dict = (await import(pathToFileURL(path.join(WEB, 'js', 'i18n', `${area}.js`)).href)).default;
  for (const f of files) {
    if (!fs.existsSync(path.join(WEB, f))) continue;
    for (const k of keys(f)) {
      total++;
      const miss = LANGS.filter(l => !(dict[l] && typeof dict[l][k] === 'string' && dict[l][k].trim()));
      if (miss.length) { bad++; if (bad <= 60) console.log(`${area} ${f}: fehlt [${miss.join(',')}] ${JSON.stringify(k).slice(0, 100)}`); }
      // Platzhalter müssen in allen Sprachen erhalten bleiben
      const ph = (k.match(/\{\w+\}/g) || []).sort().join();
      for (const l of LANGS) {
        const v = dict[l]?.[k];
        if (v && (v.match(/\{\w+\}/g) || []).sort().join() !== ph) { bad++; console.log(`${area} ${f}: Platzhalter ${l} ${JSON.stringify(k).slice(0, 80)}`); }
      }
    }
  }
}
// Weitere Sprachen: eine Datei je Sprache (js/i18n/lang/<code>.js) mit allen Bereichen
const EXTRA = ['pt', 'cs', 'sv', 'ru', 'uk', 'tr', 'ar', 'hi', 'zh', 'ja', 'ko', 'id', 'vi'];
const wantExtra = process.argv.includes('--extra') ? EXTRA : EXTRA.filter(l => fs.existsSync(path.join(WEB, 'js', 'i18n', 'lang', `${l}.js`)));
if (!only || only === 'extra') {
  const all = new Set();
  for (const files of Object.values(AREAS)) for (const f of files) if (fs.existsSync(path.join(WEB, f))) for (const k of keys(f)) all.add(k);
  for (const l of wantExtra) {
    const f = path.join(WEB, 'js', 'i18n', 'lang', `${l}.js`);
    const d = fs.existsSync(f) ? (await import(pathToFileURL(f).href)).default || {} : {};
    const miss = [...all].filter(k => !(typeof d[k] === 'string' && d[k].trim()));
    const ph = [...all].filter(k => typeof d[k] === 'string' && (k.match(/\{\w+\}/g) || []).sort().join() !== (d[k].match(/\{\w+\}/g) || []).sort().join());
    total += all.size;
    if (miss.length || ph.length) { bad += miss.length + ph.length; console.log(`${l}: ${miss.length} fehlen, ${ph.length} Platzhalter falsch${miss.length ? ' – z. B. ' + JSON.stringify(miss[0]).slice(0, 80) : ''}`); }
    else console.log(`${l}: vollständig (${all.size})`);
  }
}
console.log(bad ? `FEHLER: ${bad} Probleme bei ${total} Texten` : `OK – ${total} Texte vollständig (6 Sprachen je Bereich${wantExtra.length ? ' + ' + wantExtra.join(',') : ''})`);
process.exit(bad ? 1 : 0);
