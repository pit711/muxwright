// Kleine DOM-Helfer, Dialoge, Toasts und API-Zugriff.
import { T, LOCALE } from './i18n.js';

export function h(tag, attrs, ...children) {
  const [name, ...cls] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (cls.length) el.className = cls.join(' ');
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'tid') el.dataset.testid = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && !['list', 'form', 'type'].includes(k) && typeof v !== 'string') el[k] = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function icon(name, cls = '') {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'ico ' + cls);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------- Toasts ----------
export function toast(msg, kind = 'info', ms = 3500) {
  const box = $('#toasts');
  const t = h('div.toast.' + kind, { role: 'status' }, icon(kind === 'error' ? 'alert' : kind === 'ok' ? 'check' : 'info'), h('span', null, msg));
  box.append(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
}

// ---------- Modaler Dialog ----------
export function dialog({ title, body, ok = 'OK', cancel = T('Abbrechen'), danger = false, tid = 'dialog' }) {
  return new Promise(resolve => {
    const dlg = h('dialog.modal', { tid },
      h('form', { method: 'dialog' },
        h('h3', null, danger ? icon('alert', 'warn') : null, title),
        h('div.modal-body', null, body),
        h('div.modal-actions', null,
          cancel ? h('button.btn.ghost', { value: 'cancel', tid: tid + '-cancel' }, cancel) : null,
          h('button.btn' + (danger ? '.danger' : '.primary'), { value: 'ok', tid: tid + '-ok' }, ok))));
    document.body.append(dlg);
    dlg.addEventListener('close', () => { resolve(dlg.returnValue === 'ok'); dlg.remove(); });
    dlg.showModal();
  });
}

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, data) {
    super(data?.error || data?.message || (Array.isArray(data?.errors) ? data.errors.map(e => e.msg || e).join('; ') : `HTTP ${status}`));
    this.status = status; this.data = data;
  }
}
export async function api(method, path, body, opts = {}) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof ArrayBuffer) { init.body = body; init.headers['Content-Type'] = 'application/octet-stream'; }
    else { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
  }
  if (opts.signal) init.signal = opts.signal;
  const r = await fetch(path, init);
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = { message: txt }; }
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}

export function download(name, content, type = 'application/json') {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const a = h('a', { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function stamp(d = new Date()) {
  return d.toLocaleString(LOCALE, { dateStyle: 'medium', timeStyle: 'medium' });
}
