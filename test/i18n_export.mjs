// Exportiert die Übersetzungsvorlage für neue Sprachen: alle Dashboard-Texte (deutscher Schlüssel → englischer Text)
// und die englischen Blöcke der Einrichtung/Hardware/Komponenten/Flasher/Danke.
//   node test/i18n_export.mjs  →  web/js/i18n/_source.json
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const WEB = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'web', 'js');
const imp = f => import(pathToFileURL(path.join(WEB, f)).href);
const dict = {};
for (const a of ['a', 'b', 'c']) Object.assign(dict, (await imp(`i18n/${a}.js`)).default.en);
// Module mit Browser-Abhängigkeiten nicht importieren: Blöcke als JSON aus dem Quelltext holen wäre fehleranfällig,
// deshalb nur die reinen Datenmodule laden
const { TXT } = await imp('onboarding_i18n.js');
const { HW } = await imp('hw_i18n.js');
const { COMP } = await imp('comp_i18n.js');
const { FW } = await imp('fw_i18n.js');
const { CREDITS_T } = await imp('credits_i18n.js');
const out = { _hinweis: 'dict: deutscher Schlüssel → englischer Text (übersetzen: Wert, Schlüssel unverändert lassen). TXT/HW/COMP/FW/CREDITS: englische Blöcke, Struktur und Schlüssel gleich lassen, nur Werte übersetzen. Platzhalter {name} unverändert übernehmen.',
  dict, TXT: TXT.en, HW: HW.en, COMP: COMP.en, FW: FW.en, CREDITS: CREDITS_T.en };
fs.writeFileSync(path.join(WEB, 'i18n', '_source.json'), JSON.stringify(out, null, 1));
console.log(`_source.json: ${Object.keys(dict).length} Dashboard-Texte + Blöcke TXT/HW/COMP/FW/CREDITS`);
