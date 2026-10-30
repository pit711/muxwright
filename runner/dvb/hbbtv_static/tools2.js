/* 50 weitere Werkzeuge fuer das Muxwright-HbbTV-Portal (ES5, eigene Umsetzung, MIT).
   Bausteine: Form (Rechenformular), Table (Nachschlagen), ListEd (Abhaklisten), Saver (Vollbild-Animation). */
var DE = P.lang == 'de';
function nm(de, en) { return { de: de, en: en }; }
function num(v, d) { return fmt(v, d == null ? 2 : d); }
function money(v) { return fmt(v, 2) + ' €'; }

/* ------------------------------------------------------------------ Baustein: Rechenformular
   inputs: [[Bezeichnung, Startwert, Einheit, Auswahlliste?], ...]; calc(werte) -> [[Bezeichnung, Ergebnis], ...] */
function Form(name, desc, inputs, calc) {
  return {
    name: name, desc: desc, help: nm('▲▼/OK: Feld  ◄: löschen  ' + NUMHELP, '▲▼/OK: field  ◄: delete  ' + NUMHELP),
    start: function (el) { this.el = el; this.sel = 0; this.v = []; for (var i = 0; i < inputs.length; i++) this.v.push(inputs[i][3] ? inputs[i][1] : new NumIn(inputs[i][1])); this.draw(); },
    vals: function () { var r = [], i; for (i = 0; i < inputs.length; i++) r.push(inputs[i][3] ? this.v[i] : this.v[i].val()); return r; },
    draw: function () {
      var h = '', i, out, inp, txt;
      for (i = 0; i < inputs.length; i++) { inp = inputs[i];
        txt = inp[3] ? '◄ ' + inp[3][this.v[i]] + ' ►' : this.v[i].txt() + (inp[2] ? ' ' + inp[2] : '');
        h += '<div style="position:absolute;left:0;top:' + (i * 44) + 'px;width:744px;height:38px;line-height:38px;padding-left:12px;border-radius:7px;font-size:23px;background:' + (i == this.sel ? '#e8eef7;color:#0a1222' : '#1b2a44') + '">' +
          '<span style="display:inline-block;width:330px;opacity:0.75">' + esc(inp[0]) + '</span>' + esc(txt) + '</div>'; }
      try { out = calc(this.vals()); } catch (e) { out = [['–', tx('Eingabe prüfen', 'Check input')]]; }
      var top = inputs.length * 44 + 12;
      for (i = 0; i < out.length; i++)
        h += '<div style="position:absolute;left:' + (out.length > 6 ? (i % 2) * 380 : 0) + 'px;top:' + (top + (out.length > 6 ? Math.floor(i / 2) : i) * 36) + 'px;width:' + (out.length > 6 ? 370 : 744) + 'px;font-size:' + (out.length > 6 ? 20 : 23) + 'px;line-height:34px">' +
          '<span style="color:#9fb3d1;display:inline-block;width:' + (out.length > 6 ? 170 : 330) + 'px">' + esc(out[i][0]) + '</span><b>' + esc(String(out[i][1])) + '</b></div>';
      this.el.innerHTML = h;
    },
    key: function (k) {
      var inp = inputs[this.sel], prev = this.sel;
      if (k == KEY.UP) this.sel = (this.sel + inputs.length - 1) % inputs.length;
      else if (!inp[3] && k == KEY.LEFT) this.v[this.sel].key(KEY.YELLOW);
      else if (k == KEY.DOWN || k == KEY.OK || (!inp[3] && k == KEY.RIGHT)) this.sel = (this.sel + 1) % inputs.length;
      else if (inp[3] && (k == KEY.LEFT || k == KEY.RIGHT)) this.v[this.sel] = (this.v[this.sel] + (k == KEY.RIGHT ? 1 : inp[3].length - 1)) % inp[3].length;
      else if (!inp[3] && this.v[this.sel].key(k)) {}
      else return false;
      if (this.sel != prev && !inputs[this.sel][3]) this.v[this.sel].fresh = true;   // neues Feld: erste Ziffer ersetzt
      this.draw(); return true;
    },
    stop: function () {}
  };
}

/* ------------------------------------------------------------------ Baustein: Tabelle */
function Table(name, desc, head, widths, rows) {
  return {
    name: name, desc: desc, help: nm('▲▼ blättern', '▲▼ scroll'),
    start: function (el) { this.el = el; this.rows = typeof rows == 'function' ? rows() : rows; this.top = 0; this.draw(); },
    draw: function () {
      var h = '', i, c, x, n = 10;
      var row = function (cells, hd) { var s = '', x2 = 0; for (c = 0; c < cells.length; c++) { s += '<span style="position:absolute;left:' + x2 + 'px;white-space:nowrap">' + esc(String(cells[c])) + '</span>'; x2 += widths[c]; } return s; };
      h += '<div style="position:relative;height:34px;font-size:20px;color:#9fb3d1">' + row(head) + '</div>';
      for (i = this.top; i < Math.min(this.rows.length, this.top + n); i++)
        h += '<div style="position:relative;height:38px;line-height:38px;font-size:22px;border-top:1px solid #24406e">' + row(this.rows[i]) + '</div>';
      h += '<div style="position:absolute;right:0;top:-44px;font-size:18px;color:#9fb3d1">' + (this.top + 1) + '–' + Math.min(this.rows.length, this.top + n) + ' / ' + this.rows.length + '</div>';
      this.el.innerHTML = h;
    },
    key: function (k) { var n = 10; if (k == KEY.DOWN) this.top = Math.max(0, Math.min(this.rows.length - n, this.top + n - 1)); else if (k == KEY.UP) this.top = Math.max(0, this.top - n + 1); else return false; this.draw(); return true; },
    stop: function () {}
  };
}

/* ------------------------------------------------------------------ Baustein: Abhakliste (gespeichert auf dem Fernseher) */
function ListEd(name, desc, skey, sample) {
  return {
    name: name, desc: desc, help: nm('OK: abhaken  Grün: neuer Eintrag  Gelb: löschen  Blau: erledigte entfernen', 'OK: tick  Green: new item  Yellow: delete  Blue: remove done'),
    load: function () { try { return JSON.parse(store(skey) || 'null') || sample.slice(0); } catch (e) { return sample.slice(0); } },
    save: function () { store(skey, JSON.stringify(this.it)); },
    start: function (el) { this.el = el; this.it = this.load(); this.sel = 0; this.draw(); },
    draw: function () {
      var h = '', i, top = Math.max(0, this.sel - 9);
      if (!this.it.length) h = '<div style="font-size:24px;color:#9fb3d1">' + tx('Leer – Grün drücken für einen neuen Eintrag', 'Empty – press Green for a new item') + '</div>';
      for (i = top; i < Math.min(this.it.length, top + 10); i++)
        h += '<div class="ell" style="height:42px;line-height:42px;font-size:24px;padding-left:12px;border-radius:7px;margin-bottom:4px;background:' + (i == this.sel ? '#e8eef7;color:#0a1222' : '#1b2a44') +
          (this.it[i][1] ? ';opacity:0.55;text-decoration:line-through' : '') + '">' + (this.it[i][1] ? '☑ ' : '☐ ') + esc(this.it[i][0]) + (OSK.vis && i == this.sel ? '▌' : '') + '</div>';
      this.el.innerHTML = h;
    },
    key: function (k) {
      var me = this;
      if (OSK.vis) return OSK.key(k);
      if (k == KEY.UP) this.sel = Math.max(0, this.sel - 1);
      else if (k == KEY.DOWN) this.sel = Math.min(this.it.length - 1, this.sel + 1);
      else if (k == KEY.OK && this.it.length) this.it[this.sel][1] = !this.it[this.sel][1];
      else if (k == KEY.YELLOW && this.it.length) { this.it.splice(this.sel, 1); this.sel = Math.max(0, Math.min(this.sel, this.it.length - 1)); }
      else if (k == KEY.BLUE) { this.it = this.it.filter(function (x) { return !x[1]; }); this.sel = 0; }
      else if (k == KEY.GREEN) {
        this.it.push(['', false]); this.sel = this.it.length - 1; this.draw();
        OSK.open(function (v) {
          var e = me.it[me.sel];
          if (v == 'CLOSE' || v == 'ENTER') { OSK.close(); if (!e[0]) me.it.pop(); me.sel = Math.max(0, me.it.length - 1); me.save(); return me.draw(); }
          if (v == 'BS') e[0] = e[0].slice(0, -1); else if (v.length == 1) e[0] = (e[0] + v).slice(0, 60);
          me.draw();
        });
        return true;
      } else return false;
      this.save(); this.draw(); return true;
    },
    keypress: function (ch) { if (this.it.length) { this.it[this.sel][0] = (this.it[this.sel][0] + ch).slice(0, 60); this.save(); this.draw(); } },
    stop: function () { OSK.close(); }
  };
}

/* ------------------------------------------------------------------ Baustein: Vollbild-Animation */
function Saver(name, desc, w, h, init, frame) {
  return {
    name: name, desc: desc, help: nm('', ''), full: true,
    start: function (el) {
      var me = this;
      el.innerHTML = '<canvas id="sv" width="' + w + '" height="' + h + '" style="position:absolute;left:0;top:0;width:1280px;height:720px"></canvas>' +
        '<div id="svl" style="position:absolute;left:96px;bottom:36px;font-size:18px;color:#888">' + esc(tx('Rot: Ende', 'Red: exit')) + '</div>';
      this.c = $('sv').getContext('2d'); this.s = {}; init.call(this, this.c, w, h); this.t0 = new Date().getTime();
      this.iv = setInterval(function () { frame.call(me, me.c, w, h, (new Date().getTime() - me.t0) / 1000); }, 40);
      setTimeout(function () { var l = $('svl'); if (l) l.style.visibility = 'hidden'; }, 3000);
    },
    key: function () { return false; },
    stop: function () { clearInterval(this.iv); }
  };
}

/* ================================================================== Geld */
var T_PCT = Form(nm('Prozent', 'Percent'), nm('Anteil, Auf-/Abschlag', 'Share, mark-up/down'),
  [[tx('Grundwert', 'Base value'), '200'], [tx('Prozentsatz', 'Percentage'), '15', '%']],
  function (v) { var p = v[0] * v[1] / 100; return [[tx('Prozentwert', 'Percentage value'), num(p)], [tx('Grundwert + %', 'Base + %'), num(v[0] + p)], [tx('Grundwert − %', 'Base − %'), num(v[0] - p)]]; });
var T_VAT = Form(nm('Mehrwertsteuer', 'VAT'), nm('Netto ↔ Brutto', 'Net ↔ gross'),
  [[tx('Betrag', 'Amount'), '100', '€'], [tx('Steuersatz', 'Tax rate'), '19', '%'], [tx('Betrag ist', 'Amount is'), 0, '', [tx('netto', 'net'), tx('brutto', 'gross')]]],
  function (v) { var net = v[2] ? v[0] / (1 + v[1] / 100) : v[0], gr = net * (1 + v[1] / 100); return [[tx('Netto', 'Net'), money(net)], [tx('Steuer', 'Tax'), money(gr - net)], [tx('Brutto', 'Gross'), money(gr)]]; });
var T_DISC = Form(nm('Rabatt', 'Discount'), nm('Endpreis & Ersparnis', 'Final price & saving'),
  [[tx('Preis', 'Price'), '79.90', '€'], [tx('Rabatt', 'Discount'), '20', '%'], [tx('Zusätzlich', 'Extra'), '0', '%']],
  function (v) { var a = v[0] * (1 - v[1] / 100) * (1 - v[2] / 100); return [[tx('Endpreis', 'Final price'), money(a)], [tx('Ersparnis', 'Saving'), money(v[0] - a)], [tx('Gesamtrabatt', 'Total discount'), num((1 - a / v[0]) * 100, 1) + ' %']]; });
var T_SPLIT = Form(nm('Rechnung teilen', 'Split the bill'), nm('mit Trinkgeld', 'with tip'),
  [[tx('Rechnung', 'Bill'), '86.40', '€'], [tx('Trinkgeld', 'Tip'), '10', '%'], [tx('Personen', 'People'), '4']],
  function (v) { var t = v[0] * v[1] / 100, all = v[0] + t, n = Math.max(1, Math.round(v[2])); return [[tx('Trinkgeld', 'Tip'), money(t)], [tx('Gesamt', 'Total'), money(all)], [tx('Je Person', 'Per person'), money(all / n)], [tx('Je Person gerundet', 'Per person rounded'), money(Math.ceil(all / n * 2) / 2)]]; });
var T_SAVE = Form(nm('Sparplan', 'Savings plan'), nm('Zinseszins mit Rate', 'Compound with deposits'),
  [[tx('Startkapital', 'Start capital'), '1000', '€'], [tx('Monatliche Rate', 'Monthly deposit'), '100', '€'], [tx('Zins pro Jahr', 'Interest p.a.'), '3', '%'], [tx('Jahre', 'Years'), '10']],
  function (v) { var r = v[2] / 1200, n = Math.round(v[3] * 12), k = v[0] * Math.pow(1 + r, n) + (r ? v[1] * (Math.pow(1 + r, n) - 1) / r : v[1] * n), ein = v[0] + v[1] * n;
    return [[tx('Endkapital', 'Final capital'), money(k)], [tx('Eingezahlt', 'Paid in'), money(ein)], [tx('Zinsen', 'Interest'), money(k - ein)]]; });
var T_LOAN = Form(nm('Kredit', 'Loan'), nm('Monatsrate & Zinsen', 'Monthly rate & interest'),
  [[tx('Kreditbetrag', 'Loan amount'), '20000', '€'], [tx('Sollzins pro Jahr', 'Interest p.a.'), '5.9', '%'], [tx('Laufzeit', 'Term'), '5', tx('Jahre', 'years')]],
  function (v) { var r = v[1] / 1200, n = Math.round(v[2] * 12), a = r ? v[0] * r / (1 - Math.pow(1 + r, -n)) : v[0] / n;
    return [[tx('Monatsrate', 'Monthly rate'), money(a)], [tx('Gesamtbetrag', 'Total paid'), money(a * n)], [tx('Zinskosten', 'Interest cost'), money(a * n - v[0])]]; });
var T_INFL = Form(nm('Inflation', 'Inflation'), nm('Kaufkraft im Zeitverlauf', 'Purchasing power over time'),
  [[tx('Betrag heute', 'Amount today'), '1000', '€'], [tx('Inflation pro Jahr', 'Inflation p.a.'), '2.5', '%'], [tx('Jahre', 'Years'), '10']],
  function (v) { var f = Math.pow(1 + v[1] / 100, v[2]); return [[tx('Kaufkraft danach', 'Purchasing power then'), money(v[0] / f)], [tx('Nötig für gleiche Kaufkraft', 'Needed for same power'), money(v[0] * f)], [tx('Wertverlust', 'Loss of value'), num((1 - 1 / f) * 100, 1) + ' %']]; });

/* ================================================================== Alltag */
var T_FUEL = Form(nm('Spritkosten', 'Fuel cost'), nm('Fahrt & Verbrauch', 'Trip & consumption'),
  [[tx('Strecke', 'Distance'), '350', 'km'], [tx('Verbrauch', 'Consumption'), '6.5', 'l/100 km'], [tx('Preis', 'Price'), '1.79', '€/l'], [tx('Mitfahrer', 'Passengers'), '1']],
  function (v) { var l = v[0] * v[1] / 100, k = l * v[2]; return [[tx('Liter', 'Litres'), num(l, 1)], [tx('Kosten', 'Cost'), money(k)], [tx('Pro km', 'Per km'), money(k / v[0])], [tx('Pro Person', 'Per person'), money(k / Math.max(1, v[3]))]]; });
var T_POWER = Form(nm('Stromkosten', 'Electricity cost'), nm('Gerät pro Jahr', 'Device per year'),
  [[tx('Leistung', 'Power'), '120', 'W'], [tx('Betrieb pro Tag', 'Use per day'), '5', 'h'], [tx('Strompreis', 'Price'), '35', 'ct/kWh'], [tx('Standby', 'Standby'), '0.5', 'W']],
  function (v) { var kwh = (v[0] * v[1] + v[3] * (24 - v[1])) * 365 / 1000; return [[tx('kWh pro Jahr', 'kWh per year'), num(kwh, 1)], [tx('Kosten pro Monat', 'Cost per month'), money(kwh * v[2] / 100 / 12)], [tx('Kosten pro Jahr', 'Cost per year'), money(kwh * v[2] / 100)], [tx('davon Standby', 'of which standby'), money(v[3] * (24 - v[1]) * 365 / 1000 * v[2] / 100)]]; });
var T_BMI = Form(nm('BMI', 'BMI'), nm('Körpermasseindex', 'Body mass index'),
  [[tx('Größe', 'Height'), '178', 'cm'], [tx('Gewicht', 'Weight'), '76', 'kg']],
  function (v) { var b = v[1] / Math.pow(v[0] / 100, 2), c = b < 18.5 ? tx('Untergewicht', 'Underweight') : b < 25 ? tx('Normalgewicht', 'Normal weight') : b < 30 ? tx('Übergewicht', 'Overweight') : tx('Adipositas', 'Obesity');
    return [['BMI', num(b, 1)], [tx('Einordnung (WHO)', 'Category (WHO)'), c], [tx('Normalbereich', 'Normal range'), num(18.5 * Math.pow(v[0] / 100, 2), 0) + '–' + num(24.9 * Math.pow(v[0] / 100, 2), 0) + ' kg']]; });
var T_PACE = Form(nm('Laufen', 'Running'), nm('Pace & Prognosen', 'Pace & predictions'),
  [[tx('Strecke', 'Distance'), '10', 'km'], [tx('Zeit', 'Time'), '55', 'min']],
  function (v) { var p = v[1] / v[0], pr = function (d) { var t = v[1] * Math.pow(d / v[0], 1.06); return Math.floor(t / 60) + ':' + pad(Math.floor(t % 60)) + ' h'; };
    return [['Pace', Math.floor(p) + ':' + pad(Math.round((p % 1) * 60)) + ' min/km'], [tx('Tempo', 'Speed'), num(v[0] / v[1] * 60, 1) + ' km/h'], ['5 km', pr(5)], ['10 km', pr(10)], [tx('Halbmarathon', 'Half marathon'), pr(21.0975)], ['Marathon', pr(42.195)]]; });
var T_HR = Form(nm('Herzfrequenz', 'Heart rate'), nm('Trainingszonen', 'Training zones'),
  [[tx('Alter', 'Age'), '40', tx('Jahre', 'years')], [tx('Ruhepuls', 'Resting pulse'), '60', '/min']],
  function (v) { var mx = 220 - v[0], z = function (a, b) { return Math.round(v[1] + (mx - v[1]) * a) + '–' + Math.round(v[1] + (mx - v[1]) * b) + ' /min'; };
    return [[tx('Maximalpuls (220 − Alter)', 'Max (220 − age)'), mx + ' /min'], [tx('Zone 1 Regeneration', 'Zone 1 recovery'), z(0.5, 0.6)], [tx('Zone 2 Grundlage', 'Zone 2 endurance'), z(0.6, 0.7)], [tx('Zone 3 Aerob', 'Zone 3 aerobic'), z(0.7, 0.8)], [tx('Zone 4 Schwelle', 'Zone 4 threshold'), z(0.8, 0.9)], [tx('Zone 5 Maximal', 'Zone 5 maximum'), z(0.9, 1)]]; });
var T_TV = Form(nm('TV-Diagonale', 'TV size'), nm('Maße & Sitzabstand', 'Size & viewing distance'),
  [[tx('Diagonale', 'Diagonal'), '55', tx('Zoll', 'inch')], [tx('Format', 'Format'), 0, '', ['16:9', '21:9', '4:3']]],
  function (v) { var r = [[16, 9], [21, 9], [4, 3]][v[1]], d = v[0] * 2.54, k = d / Math.sqrt(r[0] * r[0] + r[1] * r[1]);
    return [[tx('Bildbreite', 'Width'), num(k * r[0], 1) + ' cm'], [tx('Bildhöhe', 'Height'), num(k * r[1], 1) + ' cm'], [tx('Sitzabstand UHD', 'Distance UHD'), num(k * r[1] * 1.5 / 100, 1) + ' m'], [tx('Sitzabstand HD', 'Distance HD'), num(k * r[1] * 3 / 100, 1) + ' m']]; });

/* ================================================================== Elektronik & Funk */
var E12 = [1, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2];
function e12up(r) { var d = Math.pow(10, Math.floor(Math.log(r) / Math.LN10)), i; for (i = 0; i < 12; i++) if (E12[i] * d >= r - 1e-9) return E12[i] * d; return 10 * d; }
function ohm(r) { return r >= 1e6 ? num(r / 1e6, 2) + ' MΩ' : r >= 1e3 ? num(r / 1e3, 2) + ' kΩ' : num(r, 1) + ' Ω'; }
var T_OHM = Form(nm('Ohmsches Gesetz', "Ohm's law"), nm('U, I → R, P', 'U, I → R, P'),
  [[tx('Spannung U', 'Voltage U'), '12', 'V'], [tx('Strom I', 'Current I'), '500', 'mA']],
  function (v) { var i = v[1] / 1000; return [[tx('Widerstand R', 'Resistance R'), ohm(v[0] / i)], [tx('Leistung P', 'Power P'), num(v[0] * i, 3) + ' W']]; });
var T_LED = Form(nm('LED-Vorwiderstand', 'LED resistor'), nm('mit E12-Wert', 'with E12 value'),
  [[tx('Versorgung', 'Supply'), '5', 'V'], [tx('LED-Spannung', 'LED voltage'), '2', 'V'], [tx('LED-Strom', 'LED current'), '20', 'mA'], [tx('LEDs in Reihe', 'LEDs in series'), '1']],
  function (v) { var r = (v[0] - v[1] * v[3]) / (v[2] / 1000), e = e12up(r); if (r <= 0) return [[tx('Fehler', 'Error'), tx('Spannung zu klein', 'Supply too low')]];
    return [[tx('Rechnerisch', 'Calculated'), ohm(r)], [tx('Nächster E12-Wert', 'Next E12 value'), ohm(e)], [tx('Strom damit', 'Current with it'), num((v[0] - v[1] * v[3]) / e * 1000, 1) + ' mA'], [tx('Verlust im Widerstand', 'Resistor loss'), num(Math.pow((v[0] - v[1] * v[3]) / e, 2) * e * 1000, 0) + ' mW']]; });
var T_DIV = Form(nm('Spannungsteiler', 'Voltage divider'), nm('Ausgang & Querstrom', 'Output & current'),
  [[tx('Eingang', 'Input'), '12', 'V'], ['R1', '10000', 'Ω'], ['R2', '4700', 'Ω']],
  function (v) { return [[tx('Ausgang', 'Output'), num(v[0] * v[2] / (v[1] + v[2]), 3) + ' V'], [tx('Querstrom', 'Current'), num(v[0] / (v[1] + v[2]) * 1000, 3) + ' mA'], [tx('Verlust', 'Dissipation'), num(v[0] * v[0] / (v[1] + v[2]) * 1000, 1) + ' mW']]; });
var T_LC = Form(nm('LC-Resonanz', 'LC resonance'), nm('Schwingkreis', 'Tank circuit'),
  [[tx('Induktivität', 'Inductance'), '1', 'µH'], [tx('Kapazität', 'Capacitance'), '100', 'pF']],
  function (v) { var f = 1 / (2 * Math.PI * Math.sqrt(v[0] * 1e-6 * v[1] * 1e-12)); return [[tx('Resonanzfrequenz', 'Resonant frequency'), f >= 1e6 ? num(f / 1e6, 3) + ' MHz' : num(f / 1e3, 3) + ' kHz'], [tx('Wellenlänge', 'Wavelength'), num(299792458 / f, 2) + ' m'], ['XL = XC', ohm(2 * Math.PI * f * v[0] * 1e-6)]]; });
var T_LINK = Form(nm('Funkstrecke', 'Radio link'), nm('Freiraumdämpfung, Pegel', 'Free-space loss, level'),
  [[tx('Frequenz', 'Frequency'), '474', 'MHz'], [tx('Entfernung', 'Distance'), '1', 'km'], [tx('Sendeleistung', 'TX power'), '7', 'dBm'], [tx('Antennengewinne zusammen', 'Antenna gains total'), '6', 'dBi'], [tx('Kabelverluste', 'Cable losses'), '3', 'dB']],
  function (v) { var fspl = 20 * Math.log(v[1]) / Math.LN10 + 20 * Math.log(v[0]) / Math.LN10 + 32.44, rx = v[2] + v[3] - v[4] - fspl;
    return [[tx('Freiraumdämpfung', 'Free-space loss'), num(fspl, 1) + ' dB'], [tx('Empfangspegel', 'RX level'), num(rx, 1) + ' dBm'], [tx('Empfangspegel', 'RX level'), num(rx + 108.75, 1) + ' dBµV (75 Ω)'], [tx('DVB-T2 braucht etwa', 'DVB-T2 needs about'), '≥ −80 dBm']]; });
var T_HOR = Form(nm('Funkhorizont', 'Radio horizon'), nm('Sichtweite über Grund', 'Line-of-sight range'),
  [[tx('Höhe Antenne 1', 'Height antenna 1'), '30', 'm'], [tx('Höhe Antenne 2', 'Height antenna 2'), '10', 'm']],
  function (v) { var g = function (h) { return 3.57 * Math.sqrt(h); }, r = function (h) { return 4.12 * Math.sqrt(h); };
    return [[tx('Optische Sicht', 'Optical'), num(g(v[0]) + g(v[1]), 1) + ' km'], [tx('Funkhorizont (k = 4/3)', 'Radio horizon (k = 4/3)'), num(r(v[0]) + r(v[1]), 1) + ' km']]; });
var T_SWR = Form(nm('SWR', 'SWR'), nm('Anpassung der Antenne', 'Antenna match'),
  [['SWR', '1.5', ': 1']],
  function (v) { var g = (v[0] - 1) / (v[0] + 1); return [[tx('Reflexionsfaktor', 'Reflection coefficient'), num(g, 3)], [tx('Rückflussdämpfung', 'Return loss'), num(-20 * Math.log(g) / Math.LN10, 1) + ' dB'], [tx('Reflektierte Leistung', 'Reflected power'), num(g * g * 100, 1) + ' %'], [tx('Fehlanpassungsverlust', 'Mismatch loss'), num(-10 * Math.log(1 - g * g) / Math.LN10, 2) + ' dB']]; });
var COAX = [['RG58', 15, 54], ['RG213', 7, 25], ['H155', 9, 32], ['Aircell 7', 7.9, 26], ['Ecoflex 10', 4.4, 15]];
var T_COAX = Form(nm('Koaxkabel', 'Coax cable'), nm('Dämpfung (Richtwerte)', 'Loss (typical)'),
  [[tx('Kabel', 'Cable'), 2, '', COAX.map(function (c) { return c[0]; })], [tx('Frequenz', 'Frequency'), '474', 'MHz'], [tx('Länge', 'Length'), '10', 'm']],
  function (v) { var c = COAX[v[0]], k = Math.log(c[2] / c[1]) / Math.log(10), a = c[1] * Math.pow(v[1] / 100, k);
    return [[tx('Dämpfung pro 100 m', 'Loss per 100 m'), num(a, 1) + ' dB'], [tx('Dämpfung gesamt', 'Total loss'), num(a * v[2] / 100, 2) + ' dB'], [tx('Leistung am Ende', 'Power at end'), num(Math.pow(10, -a * v[2] / 1000) * 100, 0) + ' %']]; });
var T_DATA = Form(nm('Datenmenge', 'Data volume'), nm('Bitrate × Zeit', 'Bit rate × time'),
  [[tx('Bitrate', 'Bit rate'), '8', 'Mbit/s'], [tx('Dauer', 'Duration'), '90', 'min']],
  function (v) { var b = v[0] * 1e6 / 8 * v[1] * 60; return [[tx('Datenmenge', 'Volume'), num(b / 1e6, 0) + ' MB'], ['', num(b / 1e9, 2) + ' GB'], [tx('Pro Stunde', 'Per hour'), num(v[0] * 450 / 1000, 2) + ' GB']]; });

/* ================================================================== Rechnen & Zeit */
var T_DL = Form(nm('Downloadzeit', 'Download time'), nm('Größe ÷ Tempo', 'Size ÷ speed'),
  [[tx('Größe', 'Size'), '4700', 'MB'], [tx('Tempo', 'Speed'), '50', 'Mbit/s']],
  function (v) { var s = v[0] * 8 / v[1]; return [[tx('Dauer', 'Time'), hms(s * 1000, false) + (s >= 3600 ? ' h' : ' min')], [tx('Bei 80 % Auslastung', 'At 80 % efficiency'), hms(s / 0.8 * 1000, false)]]; });
var T_RULE3 = Form(nm('Dreisatz', 'Rule of three'), nm('a → b, c → ?', 'a → b, c → ?'),
  [['a', '3'], ['b', '4.50'], ['c', '7']],
  function (v) { return [[tx('x (proportional)', 'x (proportional)'), num(v[1] * v[2] / v[0], 4)], [tx('x (antiproportional)', 'x (inverse)'), num(v[0] * v[1] / v[2], 4)]]; });
var T_QUAD = Form(nm('Quadratische Gleichung', 'Quadratic equation'), nm('ax² + bx + c = 0', 'ax² + bx + c = 0'),
  [['a', '1'], ['b', '-3'], ['c', '2']],
  function (v) { var a = v[0], b = v[1], c = v[2], d = b * b - 4 * a * c;
    if (!a) return [['x', num(-c / b, 6)]];
    if (d >= 0) return [[tx('Diskriminante', 'Discriminant'), num(d, 4)], ['x₁', num((-b + Math.sqrt(d)) / (2 * a), 6)], ['x₂', num((-b - Math.sqrt(d)) / (2 * a), 6)], [tx('Scheitel', 'Vertex'), '(' + num(-b / (2 * a), 4) + ' | ' + num(c - b * b / (4 * a), 4) + ')']];
    return [[tx('Diskriminante', 'Discriminant'), num(d, 4)], ['x₁', num(-b / (2 * a), 4) + ' + ' + num(Math.sqrt(-d) / (2 * a), 4) + 'i'], ['x₂', num(-b / (2 * a), 4) + ' − ' + num(Math.sqrt(-d) / (2 * a), 4) + 'i']]; });
function roman(n) { var v = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1], s = ['M', 'CM', 'D', 'CD', 'C', 'XC', 'L', 'XL', 'X', 'IX', 'V', 'IV', 'I'], r = '', i; if (n < 1 || n > 3999) return '–'; for (i = 0; i < 13; i++) while (n >= v[i]) { r += s[i]; n -= v[i]; } return r; }
function factors(n) { var r = [], d = 2; n = Math.floor(Math.abs(n)); if (n < 2) return String(n); while (d * d <= n) { while (n % d == 0) { r.push(d); n /= d; } d++; } if (n > 1) r.push(n); return r.join(' · '); }
var T_BASE = Form(nm('Zahlensysteme', 'Number systems'), nm('Bin, Hex, Römisch, Primfaktoren', 'Bin, hex, Roman, primes'),
  [[tx('Zahl (dezimal)', 'Number (decimal)'), '2026']],
  function (v) { var n = Math.floor(Math.abs(v[0])); return [[tx('Binär', 'Binary'), n.toString(2)], [tx('Oktal', 'Octal'), n.toString(8)], ['Hex', '0x' + n.toString(16).toUpperCase()], [tx('Römisch', 'Roman'), roman(n)], [tx('Primfaktoren', 'Prime factors'), factors(n)]]; });
var T_DATE = Form(nm('Datumsrechner', 'Date calculator'), nm('Wochentag, KW, Abstand', 'Weekday, week, distance'),
  [[tx('Jahr', 'Year'), String(new Date().getFullYear() + 1)], [tx('Monat', 'Month'), '1'], [tx('Tag', 'Day'), '1']],
  function (v) { var d = new Date(Math.round(v[0]), Math.round(v[1]) - 1, Math.round(v[2])), t = new Date(), t0 = new Date(t.getFullYear(), t.getMonth(), t.getDate()), diff = Math.round((d - t0) / 86400000);
    return [[tx('Datum', 'Date'), d.getDate() + '.' + (d.getMonth() + 1) + '.' + d.getFullYear()], [tx('Wochentag', 'Weekday'), T.wd[d.getDay()]], [tx('Kalenderwoche', 'Calendar week'), isoWeek(d)],
      [diff >= 0 ? tx('Tage bis dahin', 'Days until') : tx('Tage seitdem', 'Days since'), Math.abs(diff)], [tx('Wochen', 'Weeks'), num(Math.abs(diff) / 7, 1)]]; });
var T_SLEEP = Form(nm('Schlafrechner', 'Sleep calculator'), nm('Schlafzyklen à 90 min', '90-minute cycles'),
  [[tx('Aufstehen um (Stunde)', 'Wake up (hour)'), '6'], [tx('Minute', 'Minute'), '30']],
  function (v) { var w = (Math.round(v[0]) * 60 + Math.round(v[1])) * 60000, r = [], c; for (c = 6; c >= 4; c--) { var t = new Date(w - c * 90 * 60000 - 15 * 60000 + 86400000 * 2); r.push([c + tx(' Zyklen (', ' cycles (') + num(c * 1.5, 1) + ' h)', tx('ins Bett um ', 'go to bed at ') + pad(t.getUTCHours()) + ':' + pad(t.getUTCMinutes())]); } r.push([tx('Einschlafzeit', 'Falling asleep'), tx('15 min eingerechnet', '15 min included')]); return r; });

/* ================================================================== Tabellen */
var T_NATO = Table(nm('NATO-Alphabet', 'NATO alphabet'), nm('Buchstabieren im Funk', 'Radio spelling'), [tx('Buchstabe', 'Letter'), tx('Wort', 'Word'), tx('Ziffer', 'Digit'), tx('Wort', 'Word')], [120, 220, 120, 200],
  (function () { var w = 'Alfa Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu'.split(' '), d = 'Zero One Two Three Four Five Six Seven Eight Nine'.split(' '), r = [], i; for (i = 0; i < 26; i++) r.push([String.fromCharCode(65 + i), w[i], i < 10 ? String(i) : '', i < 10 ? d[i] : '']); return r; })());
var T_DIN = Table(nm('Buchstabiertafel', 'German spelling'), nm('DIN 5009 (2022)', 'DIN 5009 (2022)'), [tx('Buchstabe', 'Letter'), tx('Wort', 'Word')], [150, 400],
  (function () { var w = 'Aachen Berlin Chemnitz Düsseldorf Essen Frankfurt Goslar Hamburg Ingelheim Jena Köln Leipzig München Nürnberg Offenbach Potsdam Quickborn Rostock Salzwedel Tübingen Unna Völklingen Wuppertal Xanten Ypsilon Zwickau'.split(' '), r = [], i; for (i = 0; i < 26; i++) r.push([String.fromCharCode(65 + i), w[i]]); r.push(['Ä', 'Umlaut Aachen'], ['Ö', 'Umlaut Offenbach'], ['Ü', 'Umlaut Unna'], ['ß', 'Eszett']); return r; })());
var T_Q = Table(nm('Q-Gruppen', 'Q codes'), nm('Amateurfunk', 'Amateur radio'), ['Q', tx('Bedeutung', 'Meaning')], [110, 600],
  [['QRA', tx('Name der Station', 'Name of station')], ['QRG', tx('Genaue Frequenz', 'Exact frequency')], ['QRK', tx('Verständlichkeit', 'Intelligibility')], ['QRL', tx('Frequenz belegt', 'Frequency busy')], ['QRM', tx('Störungen durch Stationen', 'Interference from stations')],
    ['QRN', tx('Atmosphärische Störungen', 'Atmospheric noise')], ['QRO', tx('Sendeleistung erhöhen', 'Increase power')], ['QRP', tx('Sendeleistung verringern / kleine Leistung', 'Reduce power / low power')], ['QRQ', tx('Schneller geben', 'Send faster')], ['QRS', tx('Langsamer geben', 'Send slower')],
    ['QRT', tx('Sendung einstellen', 'Stop sending')], ['QRV', tx('Bereit', 'Ready')], ['QRX', tx('Warten', 'Wait')], ['QRZ', tx('Wer ruft mich?', 'Who is calling me?')], ['QSB', tx('Schwund (Fading)', 'Fading')], ['QSL', tx('Empfangsbestätigung', 'Acknowledge receipt')],
    ['QSO', tx('Funkverbindung', 'Contact')], ['QSP', tx('Nachricht weiterleiten', 'Relay message')], ['QSY', tx('Frequenz wechseln', 'Change frequency')], ['QTH', tx('Standort', 'Location')], ['QTR', tx('Genaue Uhrzeit', 'Exact time')]]);
var T_BANDS = Table(nm('Amateurfunkbänder', 'Amateur bands'), nm('Deutschland, Auswahl', 'Germany, selection'), [tx('Band', 'Band'), tx('Bereich', 'Range')], [150, 500],
  [['2200 m', '135,7–137,8 kHz'], ['630 m', '472–479 kHz'], ['160 m', '1,810–2,000 MHz'], ['80 m', '3,500–3,800 MHz'], ['60 m', '5,3515–5,3665 MHz'], ['40 m', '7,000–7,200 MHz'], ['30 m', '10,100–10,150 MHz'],
    ['20 m', '14,000–14,350 MHz'], ['17 m', '18,068–18,168 MHz'], ['15 m', '21,000–21,450 MHz'], ['12 m', '24,890–24,990 MHz'], ['10 m', '28,000–29,700 MHz'], ['6 m', '50,000–52,000 MHz'], ['4 m', '70,150–70,200 MHz'],
    ['2 m', '144–146 MHz'], ['70 cm', '430–440 MHz'], ['23 cm', '1240–1300 MHz'], ['13 cm', '2320–2450 MHz'], ['QO-100 ↑', '2400–2450 MHz'], ['QO-100 ↓', '10489,5–10500 MHz']]);
var T_PAPER = Table(nm('Papierformate', 'Paper sizes'), nm('DIN A, B, C', 'ISO A, B, C'), [tx('Format', 'Size'), 'mm', tx('Zoll', 'inch')], [140, 240, 240],
  (function () { var r = [], s = [['A', 841, 1189], ['B', 1000, 1414], ['C', 917, 1297]], i, k, w, h; for (k = 0; k < 3; k++) { w = s[k][1]; h = s[k][2]; for (i = 0; i <= 6; i++) { if (!(k && (i < 3))) r.push([s[k][0] + i, w + ' × ' + h, num(w / 25.4, 1) + ' × ' + num(h / 25.4, 1)]); var t = Math.floor(h / 2); h = w; w = t; } } r.push(['DL', '110 × 220', '4,3 × 8,7'], ['Letter (US)', '216 × 279', '8,5 × 11'], ['Legal (US)', '216 × 356', '8,5 × 14']); return r; })());
var T_ASCII = Table(nm('ASCII-Tabelle', 'ASCII table'), nm('Zeichen 32–126', 'Characters 32–126'), ['Dez', 'Hex', tx('Zeichen', 'Char'), 'Dez', 'Hex', tx('Zeichen', 'Char')], [80, 80, 150, 80, 80, 150],
  (function () { var r = [], i; for (i = 32; i < 80; i++) r.push([i, i.toString(16).toUpperCase(), i == 32 ? 'Space' : String.fromCharCode(i), i + 48 < 127 ? i + 48 : '', i + 48 < 127 ? (i + 48).toString(16).toUpperCase() : '', i + 48 < 127 ? String.fromCharCode(i + 48) : '']); return r; })());
var T_COLOR = Table(nm('Widerstands-Farbcode', 'Resistor colour code'), nm('Ringe lesen', 'Reading the bands'), [tx('Farbe', 'Colour'), tx('Ziffer', 'Digit'), tx('Multiplikator', 'Multiplier'), tx('Toleranz', 'Tolerance')], [190, 120, 220, 160],
  [[tx('schwarz', 'black'), 0, '× 1', ''], [tx('braun', 'brown'), 1, '× 10', '± 1 %'], [tx('rot', 'red'), 2, '× 100', '± 2 %'], [tx('orange', 'orange'), 3, '× 1 k', ''], [tx('gelb', 'yellow'), 4, '× 10 k', ''], [tx('grün', 'green'), 5, '× 100 k', '± 0,5 %'],
    [tx('blau', 'blue'), 6, '× 1 M', '± 0,25 %'], [tx('violett', 'violet'), 7, '× 10 M', '± 0,1 %'], [tx('grau', 'grey'), 8, '', '± 0,05 %'], [tx('weiß', 'white'), 9, '', ''], [tx('gold', 'gold'), '', '× 0,1', '± 5 %'], [tx('silber', 'silver'), '', '× 0,01', '± 10 %']]);

/* ================================================================== Listen */
var T_TODO = ListEd(nm('To-do', 'To-do'), nm('Aufgaben abhaken', 'Tick off tasks'), 'todo', [[tx('Antenne ausrichten', 'Align the antenna'), false], [tx('Sendersuchlauf am TV', 'Channel scan on the TV'), false]]);
var T_SHOP = ListEd(nm('Einkaufsliste', 'Shopping list'), nm('Gespeichert auf dem TV', 'Saved on the TV'), 'shop', [[tx('Milch', 'Milk'), false], [tx('Brot', 'Bread'), false], [tx('Kaffee', 'Coffee'), false]]);
var T_COUNT = {
  name: nm('Zähler', 'Counter'), desc: nm('4 Strichlisten', '4 tally counters'), help: nm('◄► Zähler  OK/▲: +1  ▼: −1  Gelb: auf 0', '◄► counter  OK/▲: +1  ▼: −1  Yellow: reset'),
  start: function (el) { this.el = el; this.c = (store('counts') || '0,0,0,0').split(',').map(Number); this.sel = 0; this.draw(); },
  draw: function () { var h = '', i; for (i = 0; i < 4; i++) h += '<div style="position:absolute;left:' + (i * 190) + 'px;top:60px;width:180px;height:300px;border-radius:14px;text-align:center;background:' + (i == this.sel ? '#e8eef7;color:#0a1222' : '#1b2a44') + '"><div style="font-size:22px;margin-top:20px">' + tx('Zähler ', 'Counter ') + (i + 1) + '</div><div style="font-size:96px;margin-top:40px">' + this.c[i] + '</div></div>'; this.el.innerHTML = h; },
  key: function (k) { if (k == KEY.LEFT) this.sel = (this.sel + 3) % 4; else if (k == KEY.RIGHT) this.sel = (this.sel + 1) % 4; else if (k == KEY.OK || k == KEY.UP) { this.c[this.sel]++; beep(1200, 30, 0.1); } else if (k == KEY.DOWN) this.c[this.sel] = Math.max(0, this.c[this.sel] - 1); else if (k == KEY.YELLOW) this.c[this.sel] = 0; else return false; store('counts', this.c.join(',')); this.draw(); return true; },
  stop: function () {}
};
var T_SCORE = {
  name: nm('Punktetafel', 'Scoreboard'), desc: nm('Zwei Teams, Vollbild', 'Two teams, full screen'), full: true, help: nm('', ''),
  start: function (el) { this.el = el; this.s = [0, 0]; this.draw(); },
  draw: function () {
    var h = '', i, col = ['#c62828', '#1565c0'];
    for (i = 0; i < 2; i++) h += '<div style="position:absolute;left:' + (i * 640) + 'px;top:0;width:640px;height:720px;background:' + col[i] + ';text-align:center"><div style="font-size:44px;margin-top:60px">' + tx('Team ', 'Team ') + (i ? 'B' : 'A') + '</div><div style="font-size:300px;line-height:420px">' + this.s[i] + '</div></div>';
    h += '<div style="position:absolute;left:0;top:660px;width:1280px;text-align:center;font-size:20px">' + esc(tx('A: ◄ +1 / Grün −1   ·   B: ► +1 / Gelb −1   ·   Blau: 0:0   ·   Rot: Ende', 'A: ◄ +1 / Green −1   ·   B: ► +1 / Yellow −1   ·   Blue: 0:0   ·   Red: exit')) + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) { if (k == KEY.LEFT) this.s[0]++; else if (k == KEY.RIGHT) this.s[1]++; else if (k == KEY.GREEN) this.s[0] = Math.max(0, this.s[0] - 1); else if (k == KEY.YELLOW) this.s[1] = Math.max(0, this.s[1] - 1); else if (k == KEY.BLUE) this.s = [0, 0]; else return false; beep(900, 60, 0.15); this.draw(); return true; },
  stop: function () {}
};

/* ================================================================== Zufall */
function pick(n, max) { var a = [], i, j, t; for (i = 1; i <= max; i++) a.push(i); for (i = 0; i < n; i++) { j = i + Math.floor(Math.random() * (max - i)); t = a[i]; a[i] = a[j]; a[j] = t; } return a.slice(0, n).sort(function (x, y) { return x - y; }); }
function ball(v, col, x, y) { return '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:76px;height:76px;border-radius:38px;background:' + col + ';color:#111;text-align:center;line-height:76px;font-size:32px;font-weight:bold">' + v + '</div>'; }
var T_LOTTO = {
  name: nm('Lottozahlen', 'Lottery numbers'), desc: nm('6 aus 49, Eurojackpot', '6 of 49, Eurojackpot'), help: nm('OK: neue Zahlen', 'OK: new numbers'),
  start: function (el) { this.el = el; this.roll(); },
  roll: function () { this.a = pick(6, 49); this.sz = Math.floor(Math.random() * 10); this.e = pick(5, 50); this.ez = pick(2, 12); this.draw(); },
  draw: function () {
    var h = '<div style="font-size:24px">6 aus 49 + Superzahl</div>', i;
    for (i = 0; i < 6; i++) h += ball(this.a[i], '#ffd600', i * 90, 40); h += ball(this.sz, '#e53935', 6 * 90 + 20, 40);
    h += '<div style="position:absolute;top:150px;font-size:24px">Eurojackpot 5 aus 50 + 2 aus 12</div>';
    for (i = 0; i < 5; i++) h += ball(this.e[i], '#ffd600', i * 90, 190); for (i = 0; i < 2; i++) h += ball(this.ez[i], '#ff9100', 5 * 90 + 20 + i * 90, 190);
    h += '<div style="position:absolute;top:300px;font-size:19px;color:#9fb3d1">' + tx('Nur Zufallszahlen – jede Kombination ist gleich wahrscheinlich.', 'Random numbers only – every combination is equally likely.') + '</div>';
    this.el.innerHTML = h;
  },
  key: function (k) { if (k == KEY.OK) { this.roll(); beep(700, 40, 0.1); return true; } return false; },
  stop: function () {}
};
var T_TEAMS = Form(nm('Teams auslosen', 'Pick teams'), nm('Spieler zufällig verteilen', 'Shuffle players into teams'),
  [[tx('Spieler', 'Players'), '10'], [tx('Teams', 'Teams'), '2']],
  function (v) { var n = Math.max(1, Math.min(40, Math.round(v[0]))), t = Math.max(1, Math.min(8, Math.round(v[1]))), p = pick(n, n), i, r = [], g = [];
    for (i = 0; i < n; i++) p[i] = p[i]; for (i = n - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), x = p[i]; p[i] = p[j]; p[j] = x; }
    for (i = 0; i < t; i++) g.push([]); for (i = 0; i < n; i++) g[i % t].push(p[i]);
    for (i = 0; i < t; i++) r.push([tx('Team ', 'Team ') + String.fromCharCode(65 + i), g[i].sort(function (a, b) { return a - b; }).join(', ')]);
    r.push([tx('Neu auslosen', 'Shuffle again'), tx('Zahl ändern oder ▲▼', 'change a number or ▲▼')]); return r; });
var T_PASS = {
  name: nm('Passwort-Generator', 'Password generator'), desc: nm('Zufällig & stark', 'Random & strong'), help: nm('◄► Länge  ▲▼ Art  OK: neu', '◄► length  ▲▼ type  OK: new'),
  SETS: [['abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789', nm('Buchstaben + Ziffern', 'Letters + digits')], ['abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!$%&*+-=?@#', nm('mit Sonderzeichen', 'with symbols')], ['0123456789', nm('nur Ziffern (PIN)', 'digits only (PIN)')]],
  start: function (el) { this.el = el; this.len = 16; this.set = 1; this.gen(); },
  rnd: function (n) { try { var a = new Uint32Array(1); window.crypto.getRandomValues(a); return a[0] % n; } catch (e) { return Math.floor(Math.random() * n); } },
  gen: function () { var s = this.SETS[this.set][0], r = '', i; for (i = 0; i < this.len; i++) r += s.charAt(this.rnd(s.length)); this.pw = r; this.draw(); },
  draw: function () { var bits = this.len * Math.log(this.SETS[this.set][0].length) / Math.LN2;
    this.el.innerHTML = '<div style="font-size:24px;color:#9fb3d1">' + esc(this.SETS[this.set][1][P.lang] || this.SETS[this.set][1].en) + ' · ' + this.len + tx(' Zeichen', ' characters') + '</div>' +
      '<div style="margin-top:30px;font-family:monospace;font-size:' + (this.len > 24 ? 30 : 44) + 'px;letter-spacing:2px;word-wrap:break-word;background:#0a1222;padding:20px;border-radius:10px">' + esc(this.pw) + '</div>' +
      '<div style="margin-top:20px;font-size:22px">' + tx('Stärke: ', 'Strength: ') + Math.round(bits) + ' Bit</div>'; },
  key: function (k) { if (k == KEY.LEFT) this.len = Math.max(4, this.len - 1); else if (k == KEY.RIGHT) this.len = Math.min(40, this.len + 1); else if (k == KEY.UP || k == KEY.DOWN) this.set = (this.set + (k == KEY.DOWN ? 1 : 2)) % 3; else if (k != KEY.OK) return false; this.gen(); return true; },
  stop: function () {}
};

/* ================================================================== Takt */
var T_POMO = {
  name: nm('Pomodoro', 'Pomodoro'), desc: nm('25 min Arbeit, 5 Pause', '25 min work, 5 break'), help: nm('OK: Start/Pause  Gelb: zurücksetzen', 'OK: start/pause  Yellow: reset'),
  start: function (el) { var me = this; this.el = el; if (this.ph == null) { this.ph = 0; this.n = 0; this.left = 25 * 60000; this.end = 0; } this.iv = setInterval(function () { me.tick(); }, 500); this.draw(); },
  tick: function () { if (this.end && this.end <= new Date().getTime()) { beep(660, 300, 0.3); this.ph = this.ph ? 0 : 1; if (!this.ph) this.n++; this.left = (this.ph ? (this.n % 4 == 3 ? 15 : 5) : 25) * 60000; this.end = new Date().getTime() + this.left; } this.draw(); },
  draw: function () { var r = this.end ? this.end - new Date().getTime() : this.left;
    this.el.innerHTML = '<div style="text-align:center;font-size:30px;color:' + (this.ph ? '#7ddc7d' : '#ff8a65') + '">' + (this.ph ? tx('Pause', 'Break') : tx('Konzentriert arbeiten', 'Focus')) + '</div><div style="text-align:center;font-size:150px;line-height:220px">' + hms(Math.max(0, r), false) + '</div><div style="text-align:center;font-size:24px;color:#9fb3d1">' + tx('Erledigte Einheiten: ', 'Completed sessions: ') + this.n + (this.end ? '' : tx(' · angehalten', ' · paused')) + '</div>'; },
  key: function (k) { if (k == KEY.OK) { if (this.end) { this.left = this.end - new Date().getTime(); this.end = 0; } else this.end = new Date().getTime() + this.left; } else if (k == KEY.YELLOW) { this.ph = 0; this.n = 0; this.left = 25 * 60000; this.end = 0; } else return false; this.draw(); return true; },
  stop: function () { clearInterval(this.iv); }
};
var T_BREATH = {
  name: nm('Atemübung', 'Breathing'), desc: nm('4-7-8 zum Entspannen', '4-7-8 to relax'), help: nm('◄► Muster', '◄► pattern'),
  PAT: [[4, 7, 8], [4, 4, 4], [5, 0, 5]], NM: [['4-7-8', '4-7-8'], [tx('Box 4-4-4', 'Box 4-4-4'), 'Box 4-4-4'], [tx('Gleichmäßig 5-5', 'Even 5-5'), 'Even 5-5']],
  start: function (el) { var me = this; this.el = el; this.p = 0; this.t0 = new Date().getTime(); this.iv = setInterval(function () { me.draw(); }, 50); },
  draw: function () {
    var p = this.PAT[this.p], cyc = p[0] + p[1] + p[2], t = ((new Date().getTime() - this.t0) / 1000) % cyc, s, ph;
    if (t < p[0]) { s = t / p[0]; ph = tx('Einatmen', 'Breathe in'); } else if (t < p[0] + p[1]) { s = 1; ph = tx('Halten', 'Hold'); } else { s = 1 - (t - p[0] - p[1]) / p[2]; ph = tx('Ausatmen', 'Breathe out'); }
    var d = 80 + s * 300;
    this.el.innerHTML = '<div style="position:absolute;left:' + (380 - d / 2) + 'px;top:' + (220 - d / 2) + 'px;width:' + d + 'px;height:' + d + 'px;border-radius:' + d / 2 + 'px;background:rgba(89,168,255,0.35);border:3px solid #7ec8ff"></div>' +
      '<div style="position:absolute;left:0;top:200px;width:760px;text-align:center;font-size:36px">' + ph + '</div><div style="position:absolute;left:0;top:430px;width:760px;text-align:center;font-size:20px;color:#9fb3d1">' + this.NM[this.p][0] + '</div>';
  },
  key: function (k) { if (k == KEY.LEFT || k == KEY.RIGHT) { this.p = (this.p + (k == KEY.RIGHT ? 1 : 2)) % 3; this.t0 = new Date().getTime(); return true; } return false; },
  stop: function () { clearInterval(this.iv); }
};
var T_METRO = {
  name: nm('Metronom', 'Metronome'), desc: nm('Takt mit Betonung', 'Beat with accent'), help: nm('◄► Tempo  ▲▼ Taktart  OK: Start/Stopp  Ziffern: BPM', '◄► tempo  ▲▼ metre  OK: start/stop  Digits: BPM'),
  start: function (el) { this.el = el; this.bpm = 100; this.m = 4; this.on = false; this.b = 0; this.typed = ''; this.draw(); },
  run: function () { var me = this; if (this.iv) clearInterval(this.iv); if (!this.on) return; this.b = 0;
    this.iv = setInterval(function () { beep(me.b % me.m ? 1000 : 1600, 40, me.b % me.m ? 0.2 : 0.35); me.cur = me.b % me.m; me.b++; me.draw(); }, 60000 / this.bpm); },
  draw: function () { var h = '<div style="text-align:center;font-size:120px;line-height:170px">' + (this.typed ? this.typed + '▌' : this.bpm) + ' <span style="font-size:36px">BPM</span></div><div style="text-align:center">', i;
    for (i = 0; i < this.m; i++) h += '<span style="display:inline-block;width:60px;height:60px;border-radius:30px;margin:12px;background:' + (this.on && i == this.cur ? (i ? '#7ec8ff' : '#ffd600') : '#24406e') + '"></span>';
    this.el.innerHTML = h + '</div><div style="text-align:center;font-size:24px;color:#9fb3d1">' + this.m + '/4 · ' + (this.on ? tx('läuft', 'running') : tx('aus', 'off')) + '</div>'; },
  key: function (k) {
    if (k >= 48 && k <= 57) { this.typed = (this.typed + String.fromCharCode(k)).slice(0, 3); this.draw(); return true; }
    if (k == KEY.OK && this.typed) { this.bpm = Math.max(20, Math.min(300, parseInt(this.typed, 10))); this.typed = ''; this.run(); }
    else if (k == KEY.OK) { this.on = !this.on; this.run(); }
    else if (k == KEY.LEFT || k == KEY.RIGHT) { this.bpm = Math.max(20, Math.min(300, this.bpm + (k == KEY.RIGHT ? 4 : -4))); this.run(); }
    else if (k == KEY.UP || k == KEY.DOWN) { this.m = Math.max(2, Math.min(7, this.m + (k == KEY.UP ? 1 : -1))); this.run(); }
    else return false;
    this.draw(); return true;
  },
  stop: function () { if (this.iv) clearInterval(this.iv); }
};

/* ================================================================== Himmel (Ort aus dem Wetter, sonst Berlin) */
function geo() { return P.geo || { lat: 52.52, lon: 13.405, place: 'Berlin' }; }
function sunTimes(d, lat, lon) {   // NOAA-Naeherung, Zenit 90,833 Grad; Ergebnis in UTC-Minuten
  var rad = Math.PI / 180, n = Math.floor((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(d.getFullYear(), 0, 0)) / 86400000);
  var g = 2 * Math.PI / 365 * (n - 1), eq = 229.18 * (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  var decl = 0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) - 0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) - 0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  var ha = function (z) { var c = Math.cos(z * rad) / (Math.cos(lat * rad) * Math.cos(decl)) - Math.tan(lat * rad) * Math.tan(decl); return c > 1 || c < -1 ? null : Math.acos(c) / rad; };
  var noon = 720 - 4 * lon - eq, h1 = ha(90.833), h2 = ha(96);
  return { rise: h1 == null ? null : noon - 4 * h1, set: h1 == null ? null : noon + 4 * h1, noon: noon, dawn: h2 == null ? null : noon - 4 * h2, dusk: h2 == null ? null : noon + 4 * h2, decl: decl / rad };
}
function utcMin(d, m) { if (m == null) return '–'; var t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) + m * 60000); return pad(t.getHours()) + ':' + pad(t.getMinutes()); }
var T_SUN = {
  name: nm('Sonne', 'Sun'), desc: nm('Auf- und Untergang', 'Sunrise & sunset'), help: nm('◄► Tag', '◄► day'),
  start: function (el) { this.el = el; this.off = 0; this.draw(); },
  draw: function () {
    var g = geo(), d = new Date(new Date().getTime() + this.off * 86400000), s = sunTimes(d, g.lat, g.lon), len = s.rise == null ? null : s.set - s.rise;
    var row = function (a, b) { return '<div style="font-size:26px;line-height:44px"><span style="display:inline-block;width:320px;color:#9fb3d1">' + a + '</span>' + b + '</div>'; };
    this.el.innerHTML = '<div style="font-size:24px;margin-bottom:10px">' + esc(g.place) + ' · ' + T.wd[d.getDay()] + ', ' + d.getDate() + '.' + (d.getMonth() + 1) + '.' + d.getFullYear() + '</div>' +
      row(tx('Morgendämmerung', 'Dawn'), utcMin(d, s.dawn)) + row(tx('Sonnenaufgang', 'Sunrise'), utcMin(d, s.rise)) + row(tx('Mittag (Höchststand)', 'Solar noon'), utcMin(d, s.noon)) +
      row(tx('Sonnenuntergang', 'Sunset'), utcMin(d, s.set)) + row(tx('Abenddämmerung', 'Dusk'), utcMin(d, s.dusk)) + row(tx('Tageslänge', 'Day length'), len == null ? '–' : Math.floor(len / 60) + ' h ' + pad(Math.round(len % 60)) + ' min') +
      row(tx('Max. Sonnenhöhe', 'Max. altitude'), num(90 - Math.abs(g.lat - s.decl), 1) + '°') + '<div style="font-size:17px;color:#6f86a8">' + tx('Uhrzeiten in der Zeitzone des Fernsehers, Näherung ±2 min', 'Times in the TV time zone, approx. ±2 min') + '</div>';
  },
  key: function (k) { if (k == KEY.LEFT) this.off--; else if (k == KEY.RIGHT) this.off++; else if (k == KEY.GREEN) this.off = 0; else return false; this.draw(); return true; },
  stop: function () {}
};
var T_MOON = {
  name: nm('Mond', 'Moon'), desc: nm('Phase & nächster Vollmond', 'Phase & next full moon'), help: nm('◄► Tag', '◄► day'),
  SYN: 29.530588853, REF: Date.UTC(2000, 0, 6, 18, 14),
  start: function (el) { this.el = el; this.off = 0; this.draw(); },
  age: function (t) { var a = ((t - this.REF) / 86400000) % this.SYN; return a < 0 ? a + this.SYN : a; },
  draw: function () {
    var t = new Date().getTime() + this.off * 86400000, a = this.age(t), f = a / this.SYN, ill = (1 - Math.cos(2 * Math.PI * f)) / 2;
    var names = DE ? ['Neumond', 'Zunehmende Sichel', 'Erstes Viertel', 'Zunehmender Mond', 'Vollmond', 'Abnehmender Mond', 'Letztes Viertel', 'Abnehmende Sichel'] : ['New moon', 'Waxing crescent', 'First quarter', 'Waxing gibbous', 'Full moon', 'Waning gibbous', 'Last quarter', 'Waning crescent'];
    var ph = names[Math.floor((f * 8 + 0.5)) % 8], nf = new Date(t + ((0.5 - f + 1) % 1) * this.SYN * 86400000), nn = new Date(t + ((1 - f) % 1 || 1) * this.SYN * 86400000), dd = new Date(t);
    this.el.innerHTML = '<canvas id="moon" width="260" height="260" style="position:absolute;left:480px;top:10px"></canvas><div style="font-size:24px;margin-bottom:10px">' + T.wd[dd.getDay()] + ', ' + dd.getDate() + '.' + (dd.getMonth() + 1) + '.' + dd.getFullYear() + '</div>' +
      '<div style="font-size:34px;line-height:52px">' + ph + '</div><div style="font-size:24px;line-height:40px">' + tx('Beleuchtet ', 'Illuminated ') + Math.round(ill * 100) + ' %<br/>' + tx('Mondalter ', 'Moon age ') + num(a, 1) + tx(' Tage', ' days') + '<br/>' +
      tx('Nächster Vollmond ', 'Next full moon ') + nf.getDate() + '.' + (nf.getMonth() + 1) + '.<br/>' + tx('Nächster Neumond ', 'Next new moon ') + nn.getDate() + '.' + (nn.getMonth() + 1) + '.</div>';
    var c = $('moon').getContext('2d'), R = 110, x0 = 130, y0 = 130;
    c.fillStyle = '#20283a'; c.beginPath(); c.arc(x0, y0, R, 0, 7); c.fill();
    c.fillStyle = '#f2ecd0'; c.beginPath();
    var waxing = f < 0.5, k = Math.cos(2 * Math.PI * f);
    c.arc(x0, y0, R, -Math.PI / 2, Math.PI / 2, !waxing);   // beleuchtete Haelfte (Nordhalbkugel: zunehmend rechts)
    c.ellipse ? c.ellipse(x0, y0, Math.abs(k) * R, R, 0, Math.PI / 2, -Math.PI / 2, (k > 0) == waxing) : null;
    c.fill();
  },
  key: function (k) { if (k == KEY.LEFT) this.off--; else if (k == KEY.RIGHT) this.off++; else if (k == KEY.GREEN) this.off = 0; else return false; this.draw(); return true; },
  stop: function () {}
};

/* ================================================================== Vollbild */
var T_BIGCLOCK = Saver(nm('Großuhr', 'Big clock'), nm('Digital im Vollbild', 'Digital, full screen'), 1280, 720, function () {}, function (c, w, h) {
  var d = new Date(); c.fillStyle = '#000'; c.fillRect(0, 0, w, h); c.fillStyle = '#e8eef7'; c.textAlign = 'center';
  c.font = 'bold 300px Arial'; c.fillText(pad(d.getHours()) + ':' + pad(d.getMinutes()), w / 2, 420);
  c.font = '48px Arial'; c.fillStyle = '#7ec8ff'; c.fillText(pad(d.getSeconds()), w / 2, 500);
  c.font = '40px Arial'; c.fillStyle = '#9fb3d1'; c.fillText(T.wd[d.getDay()] + ', ' + d.getDate() + '. ' + T.mon[d.getMonth()] + ' ' + d.getFullYear(), w / 2, 600);
});
var T_ANALOG = Saver(nm('Analoguhr', 'Analogue clock'), nm('Zeiger im Vollbild', 'Hands, full screen'), 1280, 720, function () {}, function (c, w, h) {
  var d = new Date(), x = w / 2, y = h / 2, R = 300, i, a;
  c.fillStyle = '#0a1222'; c.fillRect(0, 0, w, h); c.fillStyle = '#f5f5f0'; c.beginPath(); c.arc(x, y, R, 0, 7); c.fill();
  c.strokeStyle = '#222';
  for (i = 0; i < 60; i++) { a = i * Math.PI / 30; c.lineWidth = i % 5 ? 3 : 10; c.beginPath(); c.moveTo(x + Math.sin(a) * (R - (i % 5 ? 18 : 40)), y - Math.cos(a) * (R - (i % 5 ? 18 : 40))); c.lineTo(x + Math.sin(a) * (R - 8), y - Math.cos(a) * (R - 8)); c.stroke(); }
  var hand = function (ang, len, wid, col) { c.strokeStyle = col; c.lineWidth = wid; c.lineCap = 'round'; c.beginPath(); c.moveTo(x - Math.sin(ang) * 30, y + Math.cos(ang) * 30); c.lineTo(x + Math.sin(ang) * len, y - Math.cos(ang) * len); c.stroke(); };
  var s = d.getSeconds() + d.getMilliseconds() / 1000, m = d.getMinutes() + s / 60, hr = d.getHours() % 12 + m / 60;
  hand(hr * Math.PI / 6, 170, 16, '#222'); hand(m * Math.PI / 30, 250, 10, '#222'); hand(Math.floor(s) * Math.PI / 30, 260, 4, '#c62828');
  c.fillStyle = '#c62828'; c.beginPath(); c.arc(x, y, 12, 0, 7); c.fill();
});
var T_AQUA = Saver(nm('Aquarium', 'Aquarium'), nm('Fische im Vollbild', 'Fish, full screen'), 640, 360, function (c, w, h) {
  var i, col = ['#ff9100', '#ffd600', '#e53935', '#59a8ff', '#ab47bc', '#4caf50'];
  this.s.f = []; for (i = 0; i < 14; i++) this.s.f.push({ x: Math.random() * w, y: 40 + Math.random() * (h - 100), v: (0.4 + Math.random() * 1.2) * (Math.random() < 0.5 ? -1 : 1), s: 8 + Math.random() * 16, c: col[i % 6], p: Math.random() * 6 });
  this.s.b = []; for (i = 0; i < 25; i++) this.s.b.push({ x: Math.random() * w, y: Math.random() * h, r: 1 + Math.random() * 3 });
}, function (c, w, h, t) {
  var g = c.createLinearGradient(0, 0, 0, h), i, f, b; g.addColorStop(0, '#0b4f7a'); g.addColorStop(1, '#03203a'); c.fillStyle = g; c.fillRect(0, 0, w, h);
  c.fillStyle = '#c2a36b'; c.fillRect(0, h - 22, w, 22);
  for (i = 0; i < 9; i++) { c.strokeStyle = '#2e7d32'; c.lineWidth = 4; c.beginPath(); c.moveTo(40 + i * 70, h - 20); c.quadraticCurveTo(40 + i * 70 + Math.sin(t + i) * 18, h - 80, 40 + i * 70 + Math.sin(t * 1.3 + i) * 10, h - 120 - (i % 3) * 30); c.stroke(); }
  for (i = 0; i < this.s.f.length; i++) { f = this.s.f[i]; f.x += f.v; if (f.x < -40) f.x = w + 40; if (f.x > w + 40) f.x = -40; var y = f.y + Math.sin(t * 1.5 + f.p) * 6, dir = f.v > 0 ? 1 : -1;
    c.fillStyle = f.c; c.beginPath(); c.ellipse ? c.ellipse(f.x, y, f.s, f.s * 0.55, 0, 0, 7) : c.arc(f.x, y, f.s * 0.6, 0, 7); c.fill();
    c.beginPath(); c.moveTo(f.x - dir * f.s * 0.8, y); c.lineTo(f.x - dir * f.s * 1.6, y - f.s * 0.6); c.lineTo(f.x - dir * f.s * 1.6, y + f.s * 0.6); c.closePath(); c.fill();
    c.fillStyle = '#fff'; c.beginPath(); c.arc(f.x + dir * f.s * 0.5, y - f.s * 0.12, f.s * 0.14, 0, 7); c.fill(); c.fillStyle = '#000'; c.beginPath(); c.arc(f.x + dir * f.s * 0.55, y - f.s * 0.12, f.s * 0.07, 0, 7); c.fill(); }
  c.fillStyle = 'rgba(255,255,255,0.5)'; for (i = 0; i < this.s.b.length; i++) { b = this.s.b[i]; b.y -= 0.6 + b.r * 0.3; if (b.y < 0) { b.y = h - 20; b.x = Math.random() * w; } c.beginPath(); c.arc(b.x + Math.sin(t * 2 + i) * 2, b.y, b.r, 0, 7); c.fill(); }
});

TOOLS.push(T_PCT, T_VAT, T_DISC, T_SPLIT, T_SAVE, T_LOAN, T_INFL, T_FUEL, T_POWER, T_BMI, T_PACE, T_HR, T_TV,
  T_OHM, T_LED, T_DIV, T_LC, T_LINK, T_HOR, T_SWR, T_COAX, T_DATA, T_DL, T_RULE3, T_QUAD, T_BASE, T_DATE, T_SLEEP,
  T_NATO, T_DIN, T_Q, T_BANDS, T_PAPER, T_ASCII, T_COLOR, T_TODO, T_SHOP, T_COUNT, T_SCORE, T_LOTTO, T_TEAMS, T_PASS,
  T_POMO, T_BREATH, T_METRO, T_SUN, T_MOON, T_BIGCLOCK, T_ANALOG, T_AQUA);

/* Kategorien der Werkzeugseite; was in keiner steht, landet unter "Weitere" */
var TOOL_CATS = [
  { name: nm('Sender & TV', 'Transmitter & TV'), tools: [TermTool, TxInfo, LanTest, TestCards, ToneGen, KeyTest, Channels] },
  { name: nm('Uhr & Zeit', 'Clock & time'), tools: [Stopwatch, Timer, WorldClock, Calendar, T_POMO, T_DATE, T_BIGCLOCK, T_ANALOG, T_SUN, T_MOON] },
  { name: nm('Rechnen', 'Maths'), tools: [Calc, Units, T_PCT, T_RULE3, T_QUAD, T_BASE, T_DATA, T_DL] },
  { name: nm('Geld', 'Money'), tools: [Currency, T_VAT, T_DISC, T_SPLIT, T_SAVE, T_LOAN, T_INFL, T_FUEL, T_POWER] },
  { name: nm('Funk & Elektronik', 'Radio & electronics'), tools: [RFCalc, T_OHM, T_LED, T_DIV, T_LC, T_LINK, T_HOR, T_SWR, T_COAX, Morse, T_TV] },
  { name: nm('Tabellen', 'Tables'), tools: [T_NATO, T_DIN, T_Q, T_BANDS, T_PAPER, T_ASCII, T_COLOR] },
  { name: nm('Gesundheit', 'Health'), tools: [T_BMI, T_PACE, T_HR, T_BREATH, T_SLEEP] },
  { name: nm('Listen & Notizen', 'Lists & notes'), tools: [Notes, T_TODO, T_SHOP, T_COUNT, T_SCORE] },
  { name: nm('Zufall & Entspannung', 'Random & relax'), tools: [Dice, T_LOTTO, T_TEAMS, T_PASS, Ticker, Fire, T_AQUA, T_METRO] }
];
