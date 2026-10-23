// Bereich „Medien & Playlists“ und Jetzt-läuft-Anzeige.

import { h, icon, api, toast, dialog, download } from './ui.js';
import * as D from './dab.js';
import { S, field, card, upload, loadMedia, loadPlaylists, nowPlaying, playlistCtl, save, load, txRunning } from './core.js';
import { T } from './i18n.js';

const PL_NAME = /^[\w .()+\-]{1,64}$/u;

// ---------------------------------------------------------------- Jetzt läuft
/** Jetzt-läuft-Karte eines Dienstes; aktualisiert sich über S.npUpd */
export function nowPlayingBox(svc, o = {}) {
  const title = h('b.np-title', { tid: o.tid ? `${o.tid}-title` : 'np-title' });
  const artist = h('span.np-artist');
  const pos = h('span.np-pos.mono');
  const bar = h('div.progress.thin.np-bar', null, h('i'));
  const idx = h('small.np-idx.muted');
  const prev = h('button.btn', { type: 'button', tid: o.tid ? `${o.tid}-prev` : 'np-prev', title: T('Vorheriger Titel'), onclick: () => playlistCtl(svc.key, 'prev') }, icon('prev'));
  const next = h('button.btn', { type: 'button', tid: o.tid ? `${o.tid}-next` : 'np-next', title: T('Nächster Titel'), onclick: () => playlistCtl(svc.key, 'next') }, icon('next'));
  const box = h('div.nowplaying' + (o.cls ? '.' + o.cls : ''), { tid: o.tid || 'np-box' },
    h('div.np-ic', null, icon('music')),
    h('div.np-main', null, h('div.np-row', null, title, artist), h('div.np-row', null, bar, pos), idx),
    h('div.np-ctl', null, prev, next));
  const upd = () => {
    const np = nowPlaying(svc.key);
    const running = txRunning();
    prev.disabled = next.disabled = !running || !np;
    if (!np) {
      title.textContent = running ? T('Kein Titel gemeldet') : T('Wiedergabe gestoppt');
      artist.textContent = ''; pos.textContent = ''; idx.textContent = ''; bar.firstChild.style.width = '0';
      box.classList.add('idle'); return;
    }
    box.classList.remove('idle');
    title.textContent = np.title || (np.file || '').split('/').pop() || T('Titel {n}', { n: (np.index ?? 0) + 1 });
    artist.textContent = np.artist ? ` – ${np.artist}` : '';
    const d = Number(np.duration_s) || 0, e = Number(np.elapsed_s) || 0;
    pos.textContent = d ? `${D.fmtDur(e)} / ${D.fmtDur(d)}` : D.fmtDur(e);
    bar.firstChild.style.width = d ? `${Math.min(100, e / d * 100)}%` : '0';
    idx.textContent = (np.paused ? T('Pausiert') + ' · ' : '') + (np.index != null ? `${np.count ? T('Titel {n} von {count}', { n: np.index + 1, count: np.count }) : T('Titel {n}', { n: np.index + 1 })}${np.playlist ? ` · ${np.playlist}` : ''}` : '');
    box.classList.toggle('paused', !!np.paused);
  };
  upd();
  (S.npWatchers ||= []).push(upd);
  return box;
}

// ---------------------------------------------------------------- Seite
export function pageMedia() {
  const lib = h('div', null);
  const plBox = h('div', null);
  const refresh = async () => { await Promise.all([loadMedia(), loadPlaylists()]); drawLib(); drawPl(); };

  // ---- Bibliothek
  const fi = h('input', { type: 'file', multiple: true, hidden: true, accept: '.mp3,.ogg,.oga,.opus,.flac,.wav,.m4a,.aac,.wma,audio/*', tid: 'media-upload-input' });
  fi.addEventListener('change', async () => {
    const files = [...fi.files].filter(f => { const ok = D.AUDIO_EXT.test(f.name); if (!ok) toast(T('{file}: kein unterstütztes Audioformat', { file: f.name }), 'error'); return ok; });
    fi.value = '';
    if (!files.length) return;
    const fd = new FormData();
    for (const f of files) fd.append('file', f, f.name);
    const q = new URLSearchParams({ kind: 'audio' });
    if (pl && !dirty) q.set('playlist', pl.name);            // Server hängt die Titel direkt an
    try {
      const r = await fetch(`/api/upload?${q}`, { method: 'POST', body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok && !(j.files || []).length) throw new Error((j.errors || [j.error || `HTTP ${r.status}`]).join('; '));
      const n = (j.files || []).length || (j.file ? 1 : 0);
      toast(`${n === 1 ? T('1 Datei hochgeladen') : T('{n} Dateien hochgeladen', { n })}${pl && !dirty ? ` → „${pl.name}“` : ''}`, 'ok');
      (j.errors || []).forEach(e => toast(String(e), 'error', 6000));
      if (j.playlist && pl && j.playlist.name === pl.name) pl = { name: pl.name, items: (j.playlist.items || []).map(normItem) };
    } catch (e) { toast(T('Upload fehlgeschlagen: {msg}', { msg: e.message }), 'error', 6000); }
    await refresh();
  });
  const drawLib = () => {
    const audios = S.media.filter(m => m.kind === 'audio');
    const cur = S.playlists.find(p => p.name === S.plSel);
    lib.replaceChildren(audios.length ? h('div.tblwrap', null, h('table.tbl', { tid: 'media-list' },
      h('thead', null, h('tr', null, h('th', null, T('Datei')), h('th', null, T('Format')), h('th.r', null, T('Dauer')), h('th.r.hide-sm', null, T('Größe')), h('th', null, ''))),
      h('tbody', null, audios.map((m, i) => h('tr', { tid: `media-row-${i}` },
        h('td', null, h('b', null, m.name), m.title ? h('small.muted', null, ` ${m.artist ? m.artist + ' – ' : ''}${m.title}`) : null),
        h('td', null, h('span.tag', null, (m.format || '').toUpperCase())),
        h('td.r.mono', null, D.fmtDur(m.duration_s)), h('td.r.mono.hide-sm', null, m.size ? `${Math.round(m.size / 1024)} kB` : '–'),
        h('td.r', null, h('div.inrow.nowrap', null,
          h('button.btn.sm', { type: 'button', tid: `pl-add-${i}`, disabled: !cur, title: cur ? T('Zu „{name}“ hinzufügen', { name: cur.name }) : T('Zuerst Playlist wählen'), onclick: () => addToPlaylist(m) }, icon('plus'), h('span.hide-sm', null, 'Playlist')),
          h('button.icon-btn', { type: 'button', title: T('Datei löschen'), tid: `media-del-${i}`, onclick: () => delMedia(m) }, icon('trash')))))))))
      : h('p.muted.small', null, T('Noch keine Audiodateien. Unterstützt: MP3, OGG/Vorbis, Opus, FLAC, WAV, M4A/AAC, WMA.')));
  };
  const delMedia = async m => {
    const used = S.draft.services.filter(s => s.audio.source.file === m.path).map(s => s.label);
    if (!(await dialog({ title: T('Datei löschen?'), danger: true, ok: T('Löschen'), tid: 'confirm-media-del',
      body: [h('p', null, T('„{name}“ wird vom Server entfernt.', { name: m.name })), used.length ? h('p.alert', null, icon('alert'), T('Wird als Quelle von {users} verwendet.', { users: used.join(', ') })) : null] }))) return;
    try { await api('DELETE', `/api/media/${encodeURIComponent(m.name)}`); toast(T('Datei gelöscht'), 'ok'); } catch (e) { toast(T('Löschen fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
    await refresh();
  };

  // ---- Playlists
  let pl = S.plCur && S.plCur.name === S.plSel ? S.plCur.pl : null;   // geladene Playlist {name, items} (überlebt Neuaufbau)
  let dirty = S.plCur && S.plCur.name === S.plSel ? S.plCur.dirty : false;
  const keepPl = () => { S.plCur = pl ? { name: pl.name, pl, dirty } : null; };
  const sel = h('select', { tid: 'pl-select' });
  sel.addEventListener('change', () => openPl(sel.value));
  const openPl = async name => {
    S.plSel = name || null; save('plSel', S.plSel);
    pl = null; dirty = false;
    if (name) {
      try { const r = await api('GET', `/api/playlists/${encodeURIComponent(name)}`); pl = { name, items: (r?.items || r?.playlist?.items || []).map(normItem) }; }
      catch (e) { toast(T('Playlist nicht ladbar: {msg}', { msg: e.message }), 'error'); S.plSel = null; }
    }
    keepPl(); drawPl(); drawLib();
  };
  const normItem = it => typeof it === 'string' ? { file: it.startsWith('media/') ? it : `media/${it}`, title: '', artist: '', duration_s: null } : { title: '', artist: '', duration_s: null, ...it, exists: it.exists !== false && (it.exists === true || !S.media.length || S.media.some(m => m.path === it.file)) };
  const savePl = async () => {
    if (!pl) return;
    try {
      const r = await api('PUT', `/api/playlists/${encodeURIComponent(pl.name)}`, { name: pl.name, items: pl.items.map(({ exists, ...it }) => it) });
      if (r && Array.isArray(r.items)) pl = { name: pl.name, items: r.items.map(normItem) };
      dirty = false; toast(T('Playlist „{name}“ gespeichert', { name: pl.name }), 'ok', 2000); await loadPlaylists(); drawPl();
    }
    catch (e) { toast(T('Speichern fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
  };
  const addToPlaylist = m => {
    if (!pl) return;
    pl.items.push({ file: m.path, title: m.title || '', artist: m.artist || '', duration_s: m.duration_s ?? null });
    dirty = true; drawPl();
    toast(T('„{name}“ hinzugefügt', { name: m.name }), 'ok', 1500);
  };
  const askName = async (title, initial = '') => {
    const inp = h('input', { type: 'text', value: initial, tid: 'pl-name-input', placeholder: T('Name') });
    const ok = await dialog({ title, body: field(T('Name'), inp, { hint: T('Buchstaben, Ziffern, Leerzeichen, - _ .') }), ok: 'OK', tid: 'pl-name' });
    const v = inp.value.trim();
    if (!ok) return null;
    if (!PL_NAME.test(v)) { toast(T('Ungültiger Name'), 'error'); return null; }
    return v;
  };
  const newPl = async () => {
    const name = await askName(T('Neue Playlist')); if (!name) return;
    if (S.playlists.some(p => p.name === name)) return toast(T('Name existiert bereits'), 'error');
    try { await api('PUT', `/api/playlists/${encodeURIComponent(name)}`, { name, items: [] }); await loadPlaylists(); await openPl(name); toast(T('Playlist „{name}“ angelegt', { name }), 'ok'); }
    catch (e) { toast(e.message, 'error'); }
  };
  const renamePl = async () => {
    if (!pl) return;
    const name = await askName(T('Playlist umbenennen'), pl.name); if (!name || name === pl.name) return;
    if (S.playlists.some(p => p.name === name)) return toast(T('Name existiert bereits'), 'error');
    try {
      await api('PUT', `/api/playlists/${encodeURIComponent(name)}`, { ...pl, name });
      await api('DELETE', `/api/playlists/${encodeURIComponent(pl.name)}`);
      S.draft.services.forEach(s => { if (s.audio.source.playlist === pl.name) s.audio.source.playlist = name; });
      await loadPlaylists(); await openPl(name); toast(T('Umbenannt'), 'ok');
    } catch (e) { toast(e.message, 'error'); }
  };
  const delPl = async () => {
    if (!pl) return;
    const used = S.draft.services.filter(s => s.audio.source.kind === 'playlist' && s.audio.source.playlist === pl.name).map(s => s.label);
    if (!(await dialog({ title: T('Playlist löschen?'), danger: true, ok: T('Löschen'), tid: 'confirm-pl-del',
      body: [h('p', null, T('„{name}“ wird gelöscht.', { name: pl.name })), used.length ? h('p.alert', null, icon('alert'), T('Wird von {users} als Quelle verwendet.', { users: used.join(', ') })) : null] }))) return;
    try { await api('DELETE', `/api/playlists/${encodeURIComponent(pl.name)}`); toast(T('Playlist gelöscht'), 'ok'); await loadPlaylists(); await openPl(''); }
    catch (e) { toast(e.message, 'error'); }
  };
  const impIn = h('input', { type: 'file', accept: '.m3u,.m3u8,.pls', hidden: true, tid: 'pl-import-input' });
  impIn.addEventListener('change', async () => {
    const f = impIn.files[0]; impIn.value = '';
    if (!f) return;
    let name = pl?.name;
    if (!name) { name = await askName(T('Playlist aus Datei'), f.name.replace(/\.(m3u8?|pls)$/i, '')); if (!name) return; }
    try {
      const txt = await f.text();
      const r = await fetch(`/api/playlists/${encodeURIComponent(name)}/import`, { method: 'POST', headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body: txt });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((j.errors || [j.error || `HTTP ${r.status}`]).join('; '));
      toast(T('„{file}“ importiert: {n} Titel', { file: f.name, n: (j.items || []).length || j.count || '' }), 'ok');
      (j.warnings || j.missing || []).slice(0, 5).forEach(w => toast(typeof w === 'string' ? w : JSON.stringify(w), 'error', 6000));
      await loadPlaylists(); await openPl(name);
    } catch (e) { toast(T('Import fehlgeschlagen: {msg}', { msg: e.message }), 'error'); }
  });
  const exportPl = async () => {
    if (!pl) return;
    let txt;
    try { const r = await fetch(`/api/playlists/${encodeURIComponent(pl.name)}/export`); if (!r.ok) throw new Error(); txt = await r.text(); }
    catch { txt = D.playlistToM3U(pl); }
    download(`${pl.name}.m3u8`, txt, 'audio/mpegurl;charset=utf-8');
  };

  let dragFrom = null;
  const drawPl = () => {
    keepPl();
    sel.replaceChildren(h('option', { value: '' }, S.playlists.length ? T('Playlist wählen …') : T('keine Playlists')), ...S.playlists.map(p => h('option', { value: p.name }, (p.count != null ? `${p.name} (${p.count})` : p.name) + (p.missing ? ` · ${T('{n} fehlt', { n: p.missing })}` : ''))));
    if (pl && !S.playlists.some(x => x.name === pl.name)) sel.append(h('option', { value: pl.name }, pl.name));
    sel.value = S.plSel || '';
    const total = pl ? pl.items.reduce((a, b) => a + (Number(b.duration_s) || 0), 0) : 0;
    const users = pl ? S.draft.services.filter(s => s.audio.source.kind === 'playlist' && s.audio.source.playlist === pl.name) : [];
    plBox.replaceChildren(...[
      h('div.inrow.wrap', null, sel,
        h('button.btn', { type: 'button', tid: 'pl-new', onclick: newPl }, icon('plus'), T('Neu')),
        h('button.btn.ghost', { type: 'button', tid: 'pl-rename', disabled: !pl, onclick: renamePl }, icon('type'), h('span.hide-sm', null, T('Umbenennen'))),
        h('button.btn.ghost.danger-text', { type: 'button', tid: 'pl-del', disabled: !pl, onclick: delPl }, icon('trash'), h('span.hide-sm', null, T('Löschen')))),
      h('div.inrow.wrap', null, impIn,
        h('button.btn.sm', { type: 'button', tid: 'pl-import', onclick: () => impIn.click() }, icon('upload'), T('M3U/PLS importieren')),
        h('button.btn.sm', { type: 'button', tid: 'pl-export', disabled: !pl, onclick: exportPl }, icon('download'), T('M3U8 exportieren')),
        h('span.grow'),
        pl ? h('small.muted', null, `${T('{n} Titel', { n: pl.items.length })} · ${D.fmtDur(total)}${users.length ? ` · ${T('Quelle von {users}', { users: users.map(s => s.label).join(', ') })}` : ''}`) : null),
      !pl ? h('div.empty.small', null, icon('playlist', 'big'), h('p', null, T('Playlist wählen oder neu anlegen. Titel aus der Bibliothek links mit „Playlist“ hinzufügen.')))
        : pl.items.length ? h('ol.pl-items', { tid: 'pl-items' }, pl.items.map((it, i) => plRow(it, i)))
        : h('p.muted.small.pad', null, T('Leere Playlist – Titel aus der Bibliothek hinzufügen oder M3U importieren.')),
      pl ? h('div.inrow.wrap', null, h('span.grow'),
        dirty ? h('small.muted', null, T('ungespeicherte Änderungen')) : null,
        h('button.btn.primary', { type: 'button', tid: 'pl-save', disabled: !dirty, onclick: savePl }, icon('save'), T('Playlist speichern'))) : null].filter(Boolean));
    if (pl) users.forEach(s => { if (s.audio.source.playlist === pl.name) s.audio.source.playlist = pl.name; });
  };
  const move = (from, to) => { if (to < 0 || to >= pl.items.length || from === to) return; pl.items.splice(to, 0, pl.items.splice(from, 1)[0]); dirty = true; drawPl(); };
  const plRow = (it, i) => {
    const np = users_np(i);
    const row = h('li.pl-item' + (np ? '.playing' : '') + (it.exists === false ? '.missing' : ''), { draggable: 'true', tid: `pl-item-${i}`, title: it.exists === false ? T('Datei fehlt im Medienordner') : null },
      h('span.pl-grip', { title: T('Ziehen zum Sortieren') }, icon('grip')),
      h('span.pl-n.mono', null, i + 1),
      h('div.pl-main', null, h('b', null, it.exists === false ? icon('alert', 'warn') : null, it.title || (it.file || '').split('/').pop()), h('small.muted', null, [it.artist, it.file].filter(Boolean).join(' · '))),
      h('span.mono.muted', null, D.fmtDur(it.duration_s)),
      h('div.inrow.nowrap', null,
        h('button.icon-btn', { type: 'button', title: T('Nach oben'), disabled: i === 0, tid: `pl-up-${i}`, onclick: () => move(i, i - 1) }, icon('up')),
        h('button.icon-btn', { type: 'button', title: T('Nach unten'), disabled: i === pl.items.length - 1, tid: `pl-down-${i}`, onclick: () => move(i, i + 1) }, icon('down')),
        h('button.icon-btn', { type: 'button', title: T('Entfernen'), tid: `pl-remove-${i}`, onclick: () => { pl.items.splice(i, 1); dirty = true; drawPl(); } }, icon('x'))));
    row.addEventListener('dragstart', e => { dragFrom = i; row.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(i)); } catch { /* egal */ } });
    row.addEventListener('dragend', () => { dragFrom = null; row.classList.remove('dragging'); });
    row.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; row.classList.add('over'); });
    row.addEventListener('dragleave', () => row.classList.remove('over'));
    row.addEventListener('drop', e => { e.preventDefault(); row.classList.remove('over'); const from = dragFrom ?? Number(e.dataTransfer.getData('text/plain')); if (Number.isInteger(from)) move(from, i); });
    return row;
  };
  const users_np = i => {
    if (!pl) return false;
    return S.draft.services.some(s => s.audio.source.kind === 'playlist' && s.audio.source.playlist === pl.name && nowPlaying(s.key)?.index === i);
  };

  // Jetzt läuft für alle Playlist-Dienste
  const npSvcs = S.draft.services.filter(s => s.audio?.source?.kind === 'playlist');
  const npCard = npSvcs.length ? card(T('Jetzt läuft'), 'music', npSvcs.map(s => h('div.np-svc', null, h('small.muted', null, s.label, ' · ', s.audio.source.playlist || '–'), nowPlayingBox(s, { tid: `np-${s.key}` })))) : null;

  S.plSel = S.plSel ?? load('plSel', null);
  drawLib(); drawPl();
  setTimeout(async () => { const initial = S.plSel; await refresh(); if (S.plSel === initial && S.plSel && !pl) await openPl(S.playlists.some(p => p.name === S.plSel) ? S.plSel : ''); });

  return h('div.page-grid.media', null,
    h('div.col', null,
      card(T('Medienbibliothek'), 'folder', [
        h('div.inrow.wrap', null, fi, h('button.btn.primary', { type: 'button', tid: 'media-upload', onclick: () => fi.click() }, icon('upload'), T('Dateien hochladen')),
          h('button.btn.ghost.sm', { type: 'button', tid: 'media-refresh', onclick: refresh }, icon('refresh'), T('Aktualisieren')),
          h('span.grow'), h('small.muted', null, 'MP3 · OGG · Opus · FLAC · WAV · M4A · WMA')),
        lib], { tid: 'card-media' }),
      npCard),
    h('div.col', null, card('Playlists', 'playlist', plBox, { tid: 'card-playlists' })));
}
