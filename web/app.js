'use strict';

// ---------- pure helpers (also loaded by the node smoke test) ----------

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ESC[c]); }

// Inline markdown: `code`, **bold**, [text](http(s) url). Everything is escaped before it becomes HTML.
function inline(s) {
  const tok = [];
  const put = (html) => '\u0000' + (tok.push(html) - 1) + '\u0000';
  s = String(s).replace(/\u0000/g, '');
  s = s.replace(/`([^`\n]+)`/g, (_, c) => put('<code>' + esc(c) + '</code>'));
  s = s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, (all, text, url) =>
    /^https?:\/\//i.test(url)
      ? put('<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>')
      : all);
  s = esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // twice: a link token may hold a code token
  for (let i = 0; i < 2; i++) s = s.replace(/\u0000(\d+)\u0000/g, (_, n) => tok[n]);
  return s;
}

// Minimal block markdown: fenced code, lists, headings, tables, quotes, rules, paragraphs.
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
function cells(line) { return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim())); }

function renderMarkdown(src) {
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let code = null, list = null, para = [];
  const flushPara = () => { if (para.length) out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; };
  const flushList = () => {
    if (list) out.push('<' + list.tag + '>' + list.items.map((i) => '<li>' + inline(i) + '</li>').join('') + '</' + list.tag + '>');
    list = null;
  };
  const flush = () => { flushPara(); flushList(); };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (code) {
      if (/^\s*```/.test(line)) { out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>'); code = null; } else code.push(line);
      continue;
    }
    if (/^\s*```/.test(line)) { flush(); code = []; continue; }
    if (TABLE_ROW.test(line) && TABLE_SEP.test(lines[i + 1] || '')) {
      flush();
      const head = cells(line).map((c) => '<th>' + c + '</th>').join('');
      const body = [];
      for (i += 2; i < lines.length && TABLE_ROW.test(lines[i]); i++) body.push('<tr>' + cells(lines[i]).map((c) => '<td>' + c + '</td>').join('') + '</tr>');
      i--;
      out.push('<div class="table"><table><thead><tr>' + head + '</tr></thead><tbody>' + body.join('') + '</tbody></table></div>');
      continue;
    }
    const li = /^\s*(?:([-*+])|\d+[.)])\s+(.*)$/.exec(line);
    if (li) {
      flushPara();
      const tag = li[1] ? 'ul' : 'ol';
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(li[2]);
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) { flush(); out.push('<p class="h h' + Math.min(h[1].length, 3) + '">' + inline(h[2]) + '</p>'); continue; }
    const bq = /^\s*>\s?(.*)$/.exec(line);
    if (bq) { flush(); out.push('<blockquote>' + inline(bq[1]) + '</blockquote>'); continue; }
    flushList();
    para.push(line);
  }
  if (code) out.push('<pre><code>' + esc(code.join('\n')) + '</code></pre>');
  flush();
  return out.join('');
}

// API says "newest first, limit items" without fixing array order; accept either.
function chronological(list) {
  const a = list.slice();
  if (a.length > 1 && (a[0].ts_ms || 0) > (a[a.length - 1].ts_ms || 0)) a.reverse();
  return a;
}

// Agent badges: own simple shapes (not vendor logos); shape/letters differ, not just colour.
const AGENTS = [
  [/^claude/, '#d97757', 'star'],
  [/^codex/, '#111111', '>_'],
  [/^antigravity/, '#7c3aed', 'AG'],
  [/^opencode/, '#2f9e44', '{}'],
  [/^(zsh|bash|sh|fish|shell)$/, '#6b7280', '$'],
  [/^herdr/, '#6b7280', 'grid'],
];

// Optional logos: put image files in web/agents/ and list them here, e.g. [/^claude/, 'claude.png'].
// None ship with the project (they are the vendors' trademarks); everything falls back to the shapes above.
const LOGOS = [];

function agentIcon(agent) {
  const NS = 'http://www.w3.org/2000/svg';
  const name = String(agent || '?');
  const logo = LOGOS.find(([re]) => re.test(name.toLowerCase()));
  if (logo) {
    const img = document.createElement('img');
    img.className = 'agent-ico logo';
    img.src = 'agents/' + logo[1];
    img.alt = name;
    img.title = name;
    return img;
  }
  const hit = AGENTS.find(([re]) => re.test(name.toLowerCase()));
  const [, color, mark] = hit || [null, '#8e8e93', name.charAt(0).toUpperCase()];
  const node = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };
  const svg = node('svg', { viewBox: '0 0 16 16', class: 'agent-ico', role: 'img', 'aria-label': name });
  const title = node('title', {});
  title.textContent = name;
  svg.append(title, node('rect', { x: 0.5, y: 0.5, width: 15, height: 15, rx: 3.5, fill: color, stroke: 'rgba(255,255,255,0.35)' }));
  if (mark === 'star') {
    svg.append(node('path', { d: 'M8 3v10M3 8h10M4.5 4.5l7 7M11.5 4.5l-7 7', stroke: '#fff', 'stroke-width': 1.7, 'stroke-linecap': 'round' }));
  } else if (mark === 'grid') {
    for (const [x, y] of [[3.5, 3.5], [8.5, 3.5], [3.5, 8.5], [8.5, 8.5]]) svg.append(node('rect', { x, y, width: 4, height: 4, rx: 0.8, fill: '#fff' }));
  } else {
    const text = node('text', { x: 8, y: 8.6, 'text-anchor': 'middle', 'dominant-baseline': 'middle', fill: '#fff',
      'font-size': mark.length > 1 ? 7 : 9.5, 'font-weight': 700, 'font-family': 'ui-monospace, Menlo, monospace' });
    text.textContent = mark;
    svg.append(text);
  }
  return svg;
}

// RFC 4180-ish: quoted fields, doubled quotes, newlines inside quotes. Stops after maxRows.
function parseCsv(text, sep, maxRows) {
  const rows = [];
  let row = [], cell = '', q = false;
  for (let i = 0; i < text.length && rows.length < maxRows; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c;
    } else if (c === '"' && cell === '') q = true;
    else if (c === sep) { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); rows.push(row); row = []; cell = '';
    } else cell += c;
  }
  if ((cell || row.length) && rows.length < maxRows) { row.push(cell); rows.push(row); }
  return rows;
}

const STATUS = { working: '작업 중', blocked: '대기', done: '완료', idle: '쉼', error: '오류' };

if (typeof document !== 'undefined') main();

function main() {
  const LIMIT = 50;
  const params = new URLSearchParams(location.search);
  const MOCK = params.has('mock');
  const DEBUG = params.has('debug');
  const $ = (id) => document.getElementById(id);
  const enc = encodeURIComponent;
  const tpath = (id, rest) => '/api/terminals/' + enc(id) + '/' + rest;

  const state = {
    token: localStorage.getItem('hc_token') || (MOCK && !params.has('login') ? 'mock' : ''),
    terminals: new Map(),
    layouts: new Map(), // machine id -> herdr spaces, or null when that machine has no layout
    machines: [],
    current: null,
    docId: null,
    files: { id: null, list: [] }, // output files of state.current
    fileFrom: null, // terminal to return to from the file viewer
    seen: new Set(),
    cursor: null,
    source: null,
    loadingOlder: false,
    screenOpen: false,
    drafts: new Map(),
    attach: new Map(), // terminal id -> attachment chips
    sending: false,
    unseen: 0, // new messages below the fold
    es: null,
    retry: 1000,
    booted: false,
  };

  document.documentElement.dataset.size = 'small';
  localStorage.removeItem('hc_size');

  class ApiError extends Error {
    constructor(status, code, message) { super(message || code || 'HTTP ' + status); this.status = status; this.code = code; }
  }

  // ?debug=1: show the last request time in a corner badge (to diagnose slowness on the iPad)
  async function api(path, opts) {
    if (!DEBUG) return request(path, opts);
    const t0 = performance.now();
    try { return await request(path, opts); } finally {
      $('debug').hidden = false;
      $('debug').textContent = path.replace('/api/terminals/', '').split('?')[0] + ' ' + Math.round(performance.now() - t0) + 'ms';
    }
  }

  async function request(path, { method = 'GET', body } = {}) {
    if (MOCK) return mockApi(path, method, body);
    const headers = { Authorization: 'Bearer ' + state.token };
    if (body) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, { method, headers, body: body && JSON.stringify(body), cache: 'no-store' });
    } catch {
      throw new ApiError(0, 'network', '서버에 연결할 수 없습니다.');
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) logout('토큰이 맞지 않습니다. 다시 입력해 주세요.');
    if (!res.ok) throw new ApiError(res.status, data.error, data.message);
    return data;
  }

  async function post(path, body = {}) {
    try { await api(path, { method: 'POST', body }); return true; } catch (e) { toast(e.message); return false; }
  }

  // ---------- small UI utils ----------

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  let toastTimer;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
  }

  function ask(msg, okLabel) {
    const d = $('confirm');
    $('confirm-msg').textContent = msg;
    $('confirm-ok').textContent = okLabel;
    d.returnValue = '';
    d.showModal();
    return new Promise((resolve) => d.addEventListener('close', () => resolve(d.returnValue === 'ok'), { once: true }));
  }

  function badge(node, status) {
    node.className = 'badge ' + (status || '');
    node.textContent = STATUS[status] || '상태 없음';
  }

  function fmtTime(ms) {
    if (!ms) return '';
    const d = new Date(ms);
    const time = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    return d.toDateString() === new Date().toDateString() ? time : (d.getMonth() + 1) + '/' + d.getDate() + ' ' + time;
  }

  const cur = () => state.terminals.get(state.current);

  // ---------- login ----------

  function saveToken(t) {
    state.token = t;
    localStorage.setItem('hc_token', t);
    // EventSource cannot send headers, so the server also reads this cookie.
    document.cookie = 'hc_token=' + enc(t) + '; path=/; max-age=31536000; SameSite=Strict' + (location.protocol === 'https:' ? '; Secure' : '');
  }

  // A rejected token logs out: forget the token, its cookie and every cached conversation.
  function logout(msg) {
    state.token = '';
    localStorage.removeItem('hc_token');
    document.cookie = 'hc_token=; path=/; max-age=0';
    cacheClear();
    showLogin(msg);
  }

  // ---------- conversation cache (memory + localStorage) ----------
  // Per terminal: last /messages page and last screen text, so opening draws instantly
  // and the network result is merged in afterwards.
  const CACHE_KEY = 'hc_cache_v1';
  const CACHE_MAX = 30;
  const CACHE_CHARS = 1500000; // ~3 MB as UTF-16
  let cache = {};
  try { cache = JSON.parse(localStorage.getItem(CACHE_KEY) || '{}'); } catch { cache = {}; }

  function cachePut(id, patch) {
    cache[id] = Object.assign({}, cache[id], patch, { at: Date.now(), dirty: false });
    clearTimeout(cachePut.timer);
    cachePut.timer = setTimeout(cacheSave, 1000);
  }

  function cacheSave() {
    const keep = Object.entries(cache).sort((a, b) => b[1].at - a[1].at).slice(0, CACHE_MAX);
    while (keep.length) {
      const json = JSON.stringify(Object.fromEntries(keep));
      if (json.length <= CACHE_CHARS) {
        try { localStorage.setItem(CACHE_KEY, json); break; } catch { /* quota: drop more */ }
      }
      keep.pop(); // oldest first
    }
    if (!keep.length) localStorage.removeItem(CACHE_KEY);
    cache = Object.fromEntries(keep);
  }

  function cacheClear() {
    cache = {};
    clearTimeout(cachePut.timer);
    localStorage.removeItem(CACHE_KEY);
  }

  function setSyncing(on) { $('sync').hidden = !on; }

  // Prefetch while idle: every waiting terminal, then the 8 most recently changed, one at a time.
  let prefetchTimer = null, prefetching = false;
  function schedulePrefetch() {
    clearTimeout(prefetchTimer);
    prefetchTimer = setTimeout(() => (window.requestIdleCallback ? requestIdleCallback(prefetchRun, { timeout: 3000 }) : prefetchRun()), 1500);
  }

  async function prefetchRun() {
    if (prefetching || document.hidden || !state.token) return;
    prefetching = true;
    try {
      const ts = [...state.terminals.values()].filter((t) => t.chat && t.id !== state.current);
      const recent = (a, b) => (b.status_ms || 0) - (a.status_ms || 0);
      const waiting = ts.filter((t) => t.status === 'blocked').sort(recent);
      const others = ts.filter((t) => t.status !== 'blocked').sort(recent).slice(0, 8);
      for (const t of [...waiting, ...others]) {
        const c = cache[t.id];
        if (c && c.messages && !c.dirty && c.at >= (t.status_ms || 0)) continue;
        try {
          const d = await api(tpath(t.id, 'messages?limit=' + LIMIT));
          if (d.source !== 'screen') cachePut(t.id, { messages: d });
        } catch { break; }
      }
    } finally {
      prefetching = false;
    }
  }

  function showLogin(msg) {
    stopEvents();
    $('login').hidden = false;
    $('login-error').textContent = msg || '';
    $('token-input').focus();
  }

  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const t = $('token-input').value.trim();
    if (!t) return;
    saveToken(t);
    $('login-error').textContent = '';
    try {
      await boot();
      $('login').hidden = true;
      $('token-input').value = '';
    } catch (err) {
      if (err.status !== 401) $('login-error').textContent = err.message;
    }
  });

  // ---------- terminal list ----------

  async function loadTerminals() {
    // layout is optional: without it (old server, error) the tree falls back to where
    const [data, layout, machines] = await Promise.all([
      api('/api/terminals'), api('/api/layout').catch(() => null), api('/api/machines').catch(() => null)]);
    state.terminals = new Map((data.terminals || []).map((t) => [t.id, t]));
    state.layouts = new Map();
    if (layout && Array.isArray(layout.machines)) {
      layout.machines.forEach((m) => state.layouts.set(m.id, Array.isArray(m.spaces) ? m.spaces : null));
    } else if (layout && Array.isArray(layout.spaces)) state.layouts.set('local', layout.spaces); // single-machine shape
    state.machines = ((machines && machines.machines) || []).slice();
    const known = new Set(state.machines.map((m) => m.id));
    for (const id of [...state.layouts.keys(), ...[...state.terminals.values()].map(machineOf)]) {
      if (!known.has(id)) { known.add(id); state.machines.push({ id, label: id === 'local' ? '이 Mac' : id, state: 'connected' }); }
    }
    scheduleList();
    schedulePrefetch();
    if (state.current) {
      if (cur()) renderHeader(); else markGone();
    }
  }

  // The list is rebuilt on every status event. Rebuilding under a finger swallows the tap (the touched
  // node is gone before 'click'), so hold rebuilds while the list is touched or scrolled, and briefly after.
  let listQueued = false;
  let listTouch = false;
  let listInputAt = 0;
  function scheduleList() {
    if (listQueued) return;
    listQueued = true;
    const run = () => {
      if (listTouch || Date.now() - listInputAt < 600) { setTimeout(run, 250); return; }
      listQueued = false;
      renderList();
    };
    requestAnimationFrame(run);
  }
  const listBusy = () => { listInputAt = Date.now(); };
  $('list').addEventListener('pointerdown', () => { listTouch = true; listBusy(); }, { passive: true });
  for (const ev of ['pointerup', 'pointercancel']) window.addEventListener(ev, () => { listTouch = false; listBusy(); }, { passive: true });
  $('list').addEventListener('scroll', listBusy, { passive: true });

  // Fallback for servers without space/tab fields: split where at the first " / ".
  function placeOf(t) {
    const where = String(t.where || '');
    const cut = where.indexOf(' / ');
    const space = t.space || (cut < 0 ? where : where.slice(0, cut)) || '(위치 없음)';
    const tab = (t.tab !== undefined ? t.tab : (cut < 0 ? null : where.slice(cut + 3))) || '(탭 없음)';
    // Group by name, not id: some terminals (t4c) carry where but no herdr.space label.
    const sk = 's:' + space;
    return { space, tab, sk, tk: 't:' + sk + '|' + tab };
  }

  // Only explicit user choices are stored; everything else follows the defaults below.
  const folds = JSON.parse(localStorage.getItem('hc_folds') || '{}');
  const TAB_FOLD_AT = 5; // spaces with this many tabs start with tabs folded

  // Tree of spaces > tabs > entries. With /api/layout: herdr order, plus terminals the layout
  // doesn't know (workers, the herdr screen) under '(탭 밖)' of their where-space or '(위치 없음)'.
  // Without layout: grouped by where, sorted by name.
  const NO_SPACE = '(위치 없음)', NO_TAB = '(탭 없음)', OUTSIDE = '(탭 밖)';
  const lastIf = (name) => (x) => (x.name === name ? 1 : 0);
  const byName = (special) => (a, b) => lastIf(special)(a) - lastIf(special)(b) || a.name.localeCompare(b.name, 'ko');
  const byRecent = (a, b) => (b.t.status_ms || 0) - (a.t.status_ms || 0);

  const machineOf = (t) => t.machine || 'local';
  function machineLabel(id) {
    const m = state.machines.find((x) => x.id === id);
    return (m && m.label) || id;
  }

  function ago(ms) {
    if (!ms) return '';
    const min = Math.floor((Date.now() - ms) / 60000);
    if (min < 1) return '방금부터';
    if (min < 60) return min + '분 전부터';
    if (min < 1440) return Math.floor(min / 60) + '시간 전부터';
    return Math.floor(min / 1440) + '일 전부터';
  }

  function buildTree(mid) {
    const layout = state.layouts.get(mid);
    const terminals = [...state.terminals.values()].filter((t) => machineOf(t) === mid);
    const spaces = [];
    const placed = new Set();
    const spaceOf = new Map();
    const addSpace = (name) => {
      const sp = { key: 's:' + mid + '|' + name, name, tabs: [] };
      spaces.push(sp);
      spaceOf.set(name, sp);
      return sp;
    };
    const tabIn = (sp, name) => {
      let tab = sp.tabs.find((x) => x.name === name);
      if (!tab) sp.tabs.push((tab = { key: 't:' + sp.key + '|' + name, name, entries: [], loose: true }));
      return tab;
    };
    for (const ls of layout || []) {
      const sp = spaceOf.get(ls.name) || addSpace(ls.name || NO_SPACE);
      if (ls.id) sp.id = ls.id;
      for (const lt of ls.tabs || []) {
        const entries = [];
        for (const it of lt.items || []) {
          if (it.kind === 'terminal') {
            const t = state.terminals.get(it.id);
            if (t && !placed.has(t.id)) { placed.add(t.id); entries.push({ kind: 'terminal', t }); }
          } else if (it.kind === 'doc') entries.push({ kind: 'doc', id: it.doc_id, title: it.title || it.doc_id });
          else entries.push({ kind: 'mod', title: it.title || it.mod || '패널' });
        }
        const name = lt.name || NO_TAB;
        sp.tabs.push({ key: 't:' + sp.key + '|' + name, name, entries, id: lt.id });
      }
    }
    for (const t of terminals) {
      if (placed.has(t.id)) continue;
      const p = placeOf(t);
      const spName = layout && !spaceOf.has(p.space) ? NO_SPACE : p.space;
      tabIn(spaceOf.get(spName) || addSpace(spName), layout ? OUTSIDE : p.tab).entries.push({ kind: 'terminal', t });
    }
    if (!layout) {
      spaces.sort(byName(NO_SPACE));
      spaces.forEach((sp) => sp.tabs.sort(byName(NO_TAB)));
    } else {
      spaces.sort((a, b) => lastIf(NO_SPACE)(a) - lastIf(NO_SPACE)(b)); // stable: keeps herdr order
      spaces.forEach((sp) => sp.tabs.sort((a, b) => !!a.loose - !!b.loose));
    }
    spaces.forEach((sp) => sp.tabs.forEach((tab) => { if (tab.loose) tab.entries.sort(byRecent); }));
    return spaces;
  }

  function renderList() {
    const q = $('search').value.trim().toLowerCase();
    const has = (...xs) => xs.some((x) => String(x || '').toLowerCase().includes(q));
    const isBlocked = (e) => e.kind === 'terminal' && e.t.status === 'blocked';
    const isCur = (e) => (e.kind === 'terminal' && e.t.id === state.current) || (e.kind === 'doc' && e.id === state.docId);
    const open = (key, dflt) => (q ? true : key in folds ? !folds[key] : dflt);
    const cutoff = state.recent ? Date.now() - RECENT_MS : 0;
    const narrow = q || state.recent;
    const frag = document.createDocumentFragment();
    const waiting = [];
    const body = document.createDocumentFragment();

    for (const m of state.machines) {
      const down = m.state !== 'connected';
      const label = m.label || m.id;
      const sub = document.createDocumentFragment();
      const mEntries = [];
      for (const sp of buildTree(m.id)) {
        const tabs = [];
        for (const tab of sp.tabs) {
          const ctx = has(label, sp.name, tab.name);
          const entries = tab.entries.filter((e) => (!q || ctx || (e.kind === 'terminal'
            ? has(e.t.id, e.t.title, e.t.where, e.t.agent) : has(e.title)))
            && (!cutoff || (e.kind === 'terminal' && (e.t.status_ms || 0) >= cutoff)));
          entries.filter(isBlocked).forEach((e) => waiting.push({ e, where: label + ' · ' + sp.name + ' · ' + tab.name }));
          if (entries.length || !narrow) tabs.push({ tab, entries });
        }
        if (!tabs.length && narrow) continue;
        const all = tabs.flatMap((x) => x.entries);
        mEntries.push(...all);
        const spOpen = open(sp.key, true);
        sub.append(withAdd(foldHead('space-head', sp.key, sp.name, all, spOpen),
          !down && sp.id && { kind: 'tab', machine: m.id, space: sp.id, label: label + ' · ' + sp.name }));
        if (!spOpen) continue;
        const many = tabs.length >= TAB_FOLD_AT;
        for (const { tab, entries } of tabs) {
          const tabOpen = open(tab.key, !many || entries.some(isBlocked) || entries.some(isCur));
          sub.append(withAdd(foldHead('tab-head', tab.key, tab.name, entries, tabOpen),
            !down && sp.id && tab.id && { kind: 'panel', machine: m.id, space: sp.id, tab: tab.id,
              label: label + ' · ' + sp.name + ' · ' + tab.name }));
          if (tabOpen) entries.forEach((e) => { const n = entryNode(e); n.classList.add('in-tab'); sub.append(n); });
        }
      }
      if (narrow && !mEntries.length) continue;
      const key = 'm:' + m.id;
      const mOpen = open(key, !down);
      const head = foldHead('machine-head', key, label, mEntries, mOpen);
      const dot = el('span', 'dot' + (down ? '' : ' on'));
      dot.title = down ? '연결 안 됨' : '연결됨';
      head.insertBefore(dot, head.firstChild);
      body.append(withAdd(head, !down && { kind: 'space', machine: m.id, label }));
      if (down) body.append(el('div', 'machine-note', ['연결 안 됨', ago(m.since_ms), m.why].filter(Boolean).join(' · ')));
      if (mOpen) body.append(sub);
    }

    if (waiting.length) {
      waiting.sort((a, b) => (b.e.t.status_ms || 0) - (a.e.t.status_ms || 0));
      frag.append(el('div', 'group-title alert', '답을 기다리는 중 ' + waiting.length));
      waiting.forEach((w) => frag.append(item(w.e.t, w.where)));
    }
    frag.append(body);
    if (!body.childNodes.length) {
      frag.append(el('div', 'list-msg', q ? '검색 결과가 없습니다.' : state.recent ? '48시간 안에 움직인 에이전트가 없습니다.' : '열린 에이전트가 없습니다.'));
    }
    $('list').replaceChildren(frag);
  }

  const ADD_LABEL = { space: '새 공간', tab: '새 탭', panel: '새 칸' };
  // A fold header plus a small '+' beside it (buttons cannot nest, so they share a row).
  function withAdd(head, ctx) {
    if (!ctx) return head;
    const row = el('div', 'fold-row');
    const add = el('button', 'add-btn', '+');
    add.type = 'button';
    add.title = ADD_LABEL[ctx.kind];
    add.setAttribute('aria-label', ADD_LABEL[ctx.kind]);
    add.dataset.add = JSON.stringify(ctx);
    row.append(head, add);
    return row;
  }

  function foldHead(cls, key, name, entries, isOpen) {
    const b = el('button', 'fold ' + cls);
    b.type = 'button';
    b.dataset.fold = key;
    b.setAttribute('aria-expanded', String(isOpen));
    b.title = name;
    b.append(el('span', 'fold-name', name));
    const terms = entries.filter((e) => e.kind === 'terminal');
    if (terms.length) b.append(el('span', 'fold-count', String(terms.length)));
    const n = terms.filter((e) => e.t.status === 'blocked').length;
    if (n) b.append(el('span', 'badge blocked', '대기 ' + n));
    return b;
  }

  function entryNode(e) {
    if (e.kind === 'terminal') return item(e.t, '');
    if (e.kind === 'mod') {
      const d = el('div', 'item mod');
      d.append(el('span', 'item-title', e.title), el('span', 'item-sub', '패널'));
      return d;
    }
    const b = el('button', 'item doc');
    b.type = 'button';
    b.dataset.doc = e.id;
    if (e.id === state.docId) b.setAttribute('aria-current', 'true');
    b.append(el('span', 'item-icon', '📄'), el('span', 'item-title', e.title));
    return b;
  }

  // where: shown in the waiting group so you know which space/tab it belongs to
  function item(t, where) {
    const b = el('button', 'item' + (where ? ' is-blocked' : ''));
    b.type = 'button';
    b.dataset.id = t.id;
    if (t.id === state.current) b.setAttribute('aria-current', 'true');
    b.append(agentIcon(t.agent), el('span', 'item-title', t.title || t.id), el('span', 'item-sub', [where, t.id].filter(Boolean).join(' · ')));
    if (!t.chat) b.append(el('span', 'chip', '화면만'));
    if (!t.writable) b.append(el('span', 'chip', '보기 전용'));
    const bd = el('span');
    badge(bd, t.status);
    b.append(bd);
    return b;
  }

  function docTitle(id) {
    for (const sp of [...state.layouts.values()].flat().filter(Boolean)) {
      for (const tab of sp.tabs || []) {
        const it = (tab.items || []).find((x) => x.kind === 'doc' && x.doc_id === id);
        if (it) return { title: it.title || id, where: sp.name + ' · ' + tab.name };
      }
    }
    return { title: id, where: '' };
  }

  $('list').addEventListener('click', (e) => {
    const a = e.target.closest('.add-btn');
    if (a) { openCreate(JSON.parse(a.dataset.add)); return; }
    const f = e.target.closest('.fold');
    if (f) {
      if ($('search').value.trim()) return; // search forces everything open
      folds[f.dataset.fold] = f.getAttribute('aria-expanded') === 'true';
      localStorage.setItem('hc_folds', JSON.stringify(folds));
      renderList(); // the user asked for it: render now, not after the touch hold
      return;
    }
    const b = e.target.closest('.item');
    if (b && b.dataset.doc) location.hash = 'doc:' + enc(b.dataset.doc);
    else if (b && b.dataset.id) location.hash = enc(b.dataset.id);
  });
  $('search').addEventListener('input', () => renderList());

  // ---------- 48-hour filter ----------
  const RECENT_MS = 48 * 3600 * 1000;
  state.recent = localStorage.getItem('hc_recent') === '1';
  $('recent-btn').setAttribute('aria-pressed', String(state.recent));
  $('recent-btn').addEventListener('click', () => {
    state.recent = !state.recent;
    localStorage.setItem('hc_recent', state.recent ? '1' : '0');
    $('recent-btn').setAttribute('aria-pressed', String(state.recent));
    renderList();
  });

  // ---------- new space / tab / panel ----------
  let createCtx = null;
  function openCreate(ctx) {
    createCtx = ctx;
    $('create-title').textContent = ADD_LABEL[ctx.kind];
    $('create-where').textContent = ctx.label;
    $('create-name-row').hidden = ctx.kind === 'panel';
    $('create-folder-row').hidden = ctx.kind !== 'space';
    $('create-name').value = '';
    $('create-folder').value = '';
    $('create-error').textContent = '';
    $('create-name').required = ctx.kind !== 'panel';
    $('create-dlg').showModal();
    if (ctx.kind !== 'panel') $('create-name').focus();
  }
  $('create-form').addEventListener('submit', async (e) => {
    if (!e.submitter || e.submitter.value !== 'ok' || !createCtx) return; // cancel: the dialog closes itself
    e.preventDefault();
    const c = createCtx;
    const body = { machine: c.machine, program: $('create-program').value };
    if (c.kind !== 'panel') body.name = $('create-name').value.trim();
    if (c.kind === 'space') body.folder = $('create-folder').value.trim();
    if (c.space) body.space_id = c.space;
    if (c.tab) body.tab_id = c.tab;
    $('create-ok').disabled = true;
    try {
      const r = await api('/api/layout/' + c.kind, { method: 'POST', body });
      $('create-dlg').close();
      await loadTerminals();
      if (r.id) location.hash = enc(r.id);
    } catch (err) {
      $('create-error').textContent = err.message || '만들지 못했습니다.';
    } finally {
      $('create-ok').disabled = false;
    }
  });

  // ---------- chat ----------

  function route() {
    const h = location.hash.slice(1);
    const id = decodeURIComponent(h);
    if (h.startsWith('t:')) { location.replace('#' + h.slice(2)); return; } // from notifications
    if (h === 'chrome') openChromeList();
    else if (h.startsWith('chrome:')) openChromeTab(decodeURIComponent(h.slice(7)));
    else if (h.startsWith('doc:')) openDoc(decodeURIComponent(h.slice(4)));
    else if (h.startsWith('file:')) { const [tid, fid] = h.slice(5).split('/').map(decodeURIComponent); openFile(tid, fid); }
    else if (id && state.terminals.has(id)) openTerminal(id);
    else closeChat();
  }

  function closeChat() {
    state.fileFrom = null;
    saveDraft();
    state.current = null;
    state.docId = null;
    document.body.classList.remove('in-chat');
    $('chat').hidden = true;
    $('doc').hidden = true;
    $('empty').hidden = false;
    scheduleList();
  }

  // ---------- documents (md-viewer tabs, read only) ----------

  function openDoc(id) {
    state.fileFrom = null;
    saveDraft();
    state.current = null;
    state.docId = id;
    document.body.classList.add('in-chat');
    $('empty').hidden = true;
    $('chat').hidden = true;
    $('doc').hidden = false;
    docMode(false);
    const info = docTitle(id);
    $('doc-title').textContent = info.title;
    $('doc-sub').textContent = info.where;
    $('doc-body').textContent = '문서를 불러오는 중…';
    scheduleList();
    loadDoc();
  }

  async function loadDoc() {
    const id = state.docId;
    if (!id) return;
    try {
      const d = await api('/api/docs/' + enc(id));
      if (state.docId !== id) return;
      if (d.title) $('doc-title').textContent = d.title;
      $('doc-sub').textContent = [docTitle(id).where, d.mtime_ms && '수정 ' + fmtTime(d.mtime_ms)].filter(Boolean).join(' · ');
      $('doc-body').innerHTML = renderMarkdown(d.text); // escaped inside renderMarkdown
    } catch (e) {
      if (state.docId === id) $('doc-body').textContent = '문서를 불러오지 못했습니다: ' + e.message;
    }
  }

  $('doc-refresh').addEventListener('click', () => (state.fileFrom ? showFile() : loadDoc()));
  $('doc-back').addEventListener('click', () => { location.hash = state.fileFrom ? enc(state.fileFrom) : ''; });
  // the doc pane doubles as the file viewer; these controls belong to one mode each
  function docMode(file) {
    $('doc-open').hidden = !file;
    $('doc-body').classList.toggle('frame', false);
  }

  function saveDraft() {
    if (state.current) state.drafts.set(state.current, $('input').value);
  }

  function openTerminal(id) {
    state.docId = null;
    state.fileFrom = null;
    $('doc').hidden = true;
    const same = state.current === id;
    if (!same) {
      saveDraft();
      state.current = id;
      setSyncing(false);
      resetMessages();
      setScreenOpen(false);
      setFilesOpen(false);
      state.files = { id, list: [] };
      state.prompt = null;
      state.asking = false;
      state.askTool = null;
      $('suggest').hidden = true;
      renderFilesBadge();
      renderChips();
      $('input').value = state.drafts.get(id) || '';
      autosize();
    }
    document.body.classList.add('in-chat');
    $('empty').hidden = true;
    $('chat').hidden = false;
    autosize();
    renderHeader();
    loadPrompt();
    scheduleList();
    loadLatest(!same);
    loadFiles();
    if (!same) loadMode();
  }

  // ---------- output files ----------

  const FILE_KIND = { md: 'MD', html: 'HTML', image: 'IMG', pdf: 'PDF', csv: 'CSV', text: 'TXT' };
  const fileUrl = (fid) => (MOCK ? 'mock/files/' : '/api/files/') + enc(fid);
  const fileHash = (tid, fid) => 'file:' + enc(tid) + '/' + enc(fid);

  function fmtSize(n) {
    if (n == null) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  async function loadFiles() {
    const id = state.current;
    if (!id) return;
    try {
      const d = await api(tpath(id, 'files'));
      if (state.current !== id) return;
      state.files = { id, list: d.files || [], links: d.links || [] };
    } catch {
      if (state.current === id) state.files = { id, list: [] };
    }
    renderFilesBadge();
    if (state.filesOpen) renderFiles();
    linkifyIn($('msg-list'));
  }

  function renderFilesBadge() {
    const n = state.files.list.length + (state.files.links || []).length;
    $('files-count').textContent = n;
    $('files-count').hidden = !n;
  }

  function setFilesOpen(on) {
    state.filesOpen = on;
    if (on && state.screenOpen) setScreenOpen(false);
    $('files-pane').hidden = !on;
    $('messages').hidden = on || state.screenOpen;
    $('files-btn').setAttribute('aria-pressed', String(on));
    if (on) renderFiles();
  }

  // 정렬: 출처별(기본) · 최신순 · 종류별. 링크도 한 줄짜리 결과물로 함께 다룬다.
  const FILE_SORTS = [['source', '출처별'], ['recent', '최신순'], ['kind', '종류별']];
  let filesSort = localStorage.getItem('hc_files_sort') || 'source';

  function fileRow(f) {
    const a = el('a', 'file-row');
    if (f.url) {
      a.href = f.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
    } else a.href = '#' + fileHash(state.files.id, f.file_id);
    const k = f.kind === 'link' ? 'link' : FILE_KIND[f.kind] ? f.kind : 'text';
    const main = el('span', 'file-main');
    main.append(el('span', 'file-name', f.name), el('span', 'file-rel', f.rel || ''));
    a.append(el('span', 'kind kind-' + k, k === 'link' ? 'LINK' : FILE_KIND[k] || 'FILE'), main,
      el('span', 'file-meta', [fmtSize(f.size), fmtTime(f.time)].filter(Boolean).join(' · ')));
    return a;
  }

  function renderFiles() {
    const files = state.files.list.map((f) => ({ ...f, name: f.name || f.rel, time: f.mtime_ms }));
    const links = (state.files.links || []).filter((l) => /^https?:\/\//.test(l.url))
      .map((l) => ({ url: l.url, kind: 'link', name: l.host || l.url, rel: l.url, time: l.ts_ms, source: 'link' }));
    const all = files.concat(links);
    const newest = (a, b) => (b.time || 0) - (a.time || 0);
    let groups;
    if (filesSort === 'recent') groups = [['', all.slice().sort(newest)]];
    else if (filesSort === 'kind') {
      const order = [...Object.keys(FILE_KIND), 'link'];
      groups = order.map((k) => [k === 'link' ? '링크' : FILE_KIND[k],
        all.filter((f) => (FILE_KIND[f.kind] || f.kind === 'link' ? f.kind : 'text') === k).sort(newest)]);
    } else {
      groups = [['대화에서 언급', files.filter((f) => f.source === 'mentioned')],
        ['최근 바뀐 파일', files.filter((f) => (f.source || 'recent') === 'recent')], ['대화에 나온 링크', links]];
    }
    const frag = document.createDocumentFragment();
    if (all.length) {
      const bar = el('div', 'files-sort');
      bar.setAttribute('role', 'group');
      bar.setAttribute('aria-label', '정렬');
      for (const [key, label] of FILE_SORTS) {
        const b = el('button', 'btn', label);
        b.type = 'button';
        b.dataset.sort = key;
        b.setAttribute('aria-pressed', String(key === filesSort));
        bar.append(b);
      }
      frag.append(bar);
    }
    for (const [title, list] of groups) {
      if (!list.length) continue;
      if (title) frag.append(el('div', 'group-title', title + ' ' + list.length));
      list.forEach((f) => frag.append(fileRow(f)));
    }
    if (!all.length) frag.append(el('div', 'list-msg', '이 대화에서 찾은 결과물이 없습니다.'));
    $('files-list').replaceChildren(frag);
  }

  $('files-list').addEventListener('click', (e) => {
    const b = e.target.closest('.files-sort .btn');
    if (!b) return;
    filesSort = b.dataset.sort;
    localStorage.setItem('hc_files_sort', filesSort);
    renderFiles();
  });

  $('files-btn').addEventListener('click', () => setFilesOpen(!state.filesOpen));

  // Turn file paths in bubbles into links. DOM-only (text nodes), so message text never becomes markup.
  function linkifyIn(root) {
    const files = state.files.list;
    if (!files.length || !root) return;
    const names = new Map();
    files.forEach((f) => names.set(f.name, names.has(f.name) ? null : f)); // bare names only when unique
    const keys = new Map();
    for (const f of files) {
      if (f.rel) keys.set(f.rel, f);
      if (f.name && names.get(f.name) && f.name.length >= 4) keys.set(f.name, f);
    }
    if (!keys.size) return;
    const quote = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp('(?<![\\w.-])(' + [...keys.keys()].sort((a, b) => b.length - a.length).map(quote).join('|') + ')(?![\\w-]|\\.\\w)', 'g');
    const tid = state.files.id;
    for (const bubble of root.querySelectorAll('.bubble')) {
      const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => (n.parentElement.closest('a, pre') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
      });
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const node of nodes) {
        const text = node.nodeValue;
        re.lastIndex = 0;
        if (!re.test(text)) continue;
        re.lastIndex = 0;
        const frag = document.createDocumentFragment();
        let last = 0, m;
        while ((m = re.exec(text))) {
          frag.append(text.slice(last, m.index));
          const a = el('a', 'file-link', m[1]);
          a.href = '#' + fileHash(tid, keys.get(m[1]).file_id);
          frag.append(a);
          last = m.index + m[1].length;
        }
        frag.append(text.slice(last));
        node.replaceWith(frag);
      }
    }
  }

  async function openFile(tid, fid) {
    if (state.current !== tid || !state.files.list.length) {
      if (!state.terminals.has(tid)) return closeChat();
      openTerminal(tid);
      await loadFiles();
    }
    state.fileFrom = tid;
    state.fileId = fid;
    document.body.classList.add('in-chat');
    $('empty').hidden = true;
    $('chat').hidden = true;
    $('doc').hidden = false;
    docMode(true);
    showFile();
  }

  async function showFile() {
    const fid = state.fileId;
    const f = state.files.list.find((x) => x.file_id === fid) || { file_id: fid, name: fid, kind: 'text' };
    const url = fileUrl(fid);
    $('doc-title').textContent = f.name || fid;
    $('doc-sub').textContent = [f.rel, fmtSize(f.size), fmtTime(f.mtime_ms)].filter(Boolean).join(' · ');
    $('doc-open').href = url;
    const body = $('doc-body');
    body.classList.toggle('frame', f.kind === 'html' || f.kind === 'pdf');
    if (f.kind === 'image') {
      const img = el('img', 'file-img');
      img.src = url;
      img.alt = f.name || '';
      return body.replaceChildren(img);
    }
    if (f.kind === 'html' || f.kind === 'pdf') {
      const fr = el('iframe', 'file-frame');
      // html: scripts may run but in an opaque origin -- never add allow-same-origin
      if (f.kind === 'html') fr.setAttribute('sandbox', 'allow-scripts');
      fr.title = f.name || '';
      fr.src = url;
      const nodes = [fr];
      if (f.kind === 'pdf') nodes.unshift(el('p', 'source-note', 'iPhone·iPad 에서는 첫 쪽만 보일 수 있습니다. 전체는 위의 \'새 탭에서 열기\' 로 보세요.'));
      return body.replaceChildren(...nodes);
    }
    body.textContent = '불러오는 중…';
    let text;
    try {
      text = await fetchText(url);
    } catch (e) {
      if (state.fileId === fid) body.textContent = '파일을 불러오지 못했습니다: ' + e.message;
      return;
    }
    if (state.fileId !== fid) return;
    if (f.kind === 'md') body.innerHTML = renderMarkdown(text); // escaped inside renderMarkdown
    else if (f.kind === 'csv') body.replaceChildren(csvTable(text, /\.tsv$/i.test(f.name || '') ? '\t' : ','));
    else body.replaceChildren(el('pre', 'file-text', text));
  }

  async function fetchText(url) {
    const res = await fetch(url, { headers: MOCK ? {} : { Authorization: 'Bearer ' + state.token }, cache: 'no-store' });
    if (res.status === 401) logout('토큰이 맞지 않습니다. 다시 입력해 주세요.');
    if (!res.ok) throw new ApiError(res.status, 'http', 'HTTP ' + res.status);
    return res.text();
  }

  const CSV_MAX = 1000;
  function csvTable(text, sep) {
    const rows = parseCsv(text, sep, CSV_MAX + 1);
    const wrap = el('div', 'table');
    const table = el('table');
    rows.slice(0, CSV_MAX).forEach((r, i) => {
      const tr = el('tr');
      r.forEach((c) => tr.append(el(i ? 'td' : 'th', null, c)));
      table.append(tr);
    });
    wrap.append(table);
    if (rows.length > CSV_MAX) wrap.append(el('p', 'source-note', '처음 ' + CSV_MAX + '줄만 보여 줍니다.'));
    return wrap;
  }

  function renderHeader() {
    const t = cur();
    if (!t) return;
    $('chat-name').textContent = t.title || t.id;
    $('chat-agent').replaceChildren(agentIcon(t.agent));
    $('chat-sub').textContent = [machineLabel(machineOf(t)), t.where, t.id].filter(Boolean).join(' · ');
    badge($('chat-status'), t.status);
    $('chat-state').className = 'state ' + (t.status || '');
    $('chat-state').textContent = STATUS[t.status] || '';
    $('composer').hidden = !t.writable;
    $('readonly-note').hidden = !!t.writable;
    $('readonly-note').textContent = '이 터미널은 셸이라 여기서 보낼 수 없습니다. 화면 보기만 됩니다.';
    updateBlocked();
  }

  function markGone() {
    $('chat-status').className = 'badge';
    $('chat-status').textContent = '닫힘';
    $('chat-state').className = 'state';
    $('chat-state').textContent = '닫힘';
    $('blocked').hidden = true;
    $('composer').hidden = true;
    $('readonly-note').hidden = false;
    $('readonly-note').textContent = '이 터미널은 닫혔습니다.';
  }

  function resetMessages() {
    document.body.classList.remove('browser-mode');
    state.unseen = 0;
    state.seen.clear();
    state.cursor = null;
    state.source = null;
    $('msg-list').replaceChildren();
    $('older').textContent = '';
  }

  function nearBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 80; }

  function renderMessage(m) {
    if (m.role === 'tool') {
      const d = el('details', 'tool');
      const s = el('summary');
      const row = el('span', 'trow');
      row.append(el('span', 'tname', (m.tool && m.tool.name) || '도구'), el('span', 'tsum', (m.tool && m.tool.summary) || ''));
      s.append(row);
      d.append(s);
      if (m.text) d.append(el('pre', null, m.text));
      d.dataset.id = m.id;
      return d;
    }
    const wrap = el('div', 'msg ' + (m.role === 'user' ? 'user' : 'assistant') + (m.queued ? ' pending' : ''));
    const bubble = el('div', 'bubble');
    if (m.ask && typeof m.ask === 'object' && Array.isArray(m.ask.questions)) bubble.append(askCard(m));
    else bubble.innerHTML = renderMarkdown(m.text); // escaped inside renderMarkdown
    wrap.append(bubble, el('div', 'meta', m.queued ? '대기 중' : fmtTime(m.ts_ms)));
    wrap.dataset.id = m.id;
    if (m.queued) wrap.dataset.text = norm(m.text);
    return wrap;
  }

  // ---------- next-prompt suggestion (the grey text Claude Code puts in its empty prompt) ----------
  let suggestSeq = 0;
  async function loadSuggest() {
    const id = state.current;
    const t = cur();
    const seq = ++suggestSeq;
    const hide = () => { if (seq === suggestSeq) $('suggest').hidden = true; };
    if (!t || t.agent !== 'claude-code' || t.status === 'working' || t.status === 'blocked' || state.asking) return hide();
    try {
      const d = await api(tpath(id, 'suggest'));
      if (seq !== suggestSeq || state.current !== id) return;
      const text = d && d.text;
      $('suggest').hidden = !text || !!$('input').value.trim();
      $('suggest').textContent = text ? '💡 ' + text : '';
      $('suggest').dataset.text = text || '';
    } catch { hide(); }
  }
  $('suggest').addEventListener('click', () => {
    $('input').value = $('suggest').dataset.text || '';
    $('suggest').hidden = true;
    autosize();
    $('input').focus();
  });
  $('input').addEventListener('input', () => { if ($('input').value.trim()) $('suggest').hidden = true; });

  // ---------- permission mode (Claude Code: shift+tab cycle, Codex: /permissions picker; the server presses the keys) ----------
  const MODE_AGENTS = new Set(['claude-code', 'codex']);
  const RISKY_MODES = new Set(['bypass', 'full']);
  let modeSeq = 0;
  async function loadMode() {
    const id = state.current, t = cur(), seq = ++modeSeq;
    state.mode = null;
    $('mode-btn').hidden = !t || !t.writable || !MODE_AGENTS.has(t.agent);
    $('mode-lbl').textContent = '권한';
    if ($('mode-btn').hidden) return;
    try {
      const d = await api(tpath(id, 'mode'));
      if (seq !== modeSeq || state.current !== id) return;
      state.mode = d;
      const m = d.modes.find((x) => x.id === d.current);
      $('mode-lbl').textContent = m ? m.label : '권한';
    } catch { /* the button still opens the picker */ }
  }
  $('mode-btn').addEventListener('click', async () => {
    const id = state.current;
    $('mode-note').textContent = '지금 모드를 읽는 중…';
    $('mode-list').replaceChildren();
    $('mode-dlg').showModal();
    await loadMode();
    const d = state.mode;
    if (state.current !== id || !$('mode-dlg').open) return;
    if (!d) { $('mode-note').textContent = '모드를 읽지 못했습니다. 잠시 뒤 다시 열어 주세요.'; return; }
    $('mode-note').textContent = d.current ? ''
      : (cur() || {}).agent === 'codex' ? 'Codex 는 화면에 모드가 나오지 않아 지금 모드를 모릅니다. 고르면 바꾼 뒤 확인합니다.'
      : '화면에서 지금 모드를 찾지 못했습니다.';
    $('mode-list').replaceChildren(...d.modes.map((m) => {
      const b = el('button', 'btn mode-opt' + (m.id === d.current ? ' primary' : ''));
      b.type = 'button';
      b.append(el('span', null, m.label + (m.id === d.current ? ' ✓' : '')), el('span', 'desc', m.desc));
      b.addEventListener('click', () => setMode(id, m, d.current));
      return b;
    }));
  });
  $('mode-close').addEventListener('click', () => $('mode-dlg').close());

  async function setMode(id, m, current) {
    $('mode-dlg').close();
    if (m.id === current) return;
    if (RISKY_MODES.has(m.id) && !(await ask(`'${m.label}' 로 바꿀까요?\n${m.desc}`, '바꾸기'))) return;
    toast('권한 모드를 바꾸는 중… (Codex 는 20초쯤)');
    $('mode-btn').disabled = true;
    $('mode-lbl').textContent = '바꾸는 중…';
    try {
      await api(tpath(id, 'mode'), { method: 'POST', body: { mode: m.id } });
      toast('권한 모드: ' + m.label);
    } catch (e) { toast(e.message); }
    $('mode-btn').disabled = false;
    if (state.current === id) loadMode();
  }

  // ---------- Herdr browser (terminal-browser): sized to this screen while viewed, tap/type/scroll go to the page ----------
  let browserTimer = null;
  function showBrowser(base) {
    const id = state.current;
    base = base || tpath(id, 'browser');
    resetMessages();
    state.source = 'browser';
    document.body.classList.add('browser-mode');
    const box = el('div', 'browser-view');
    const bar = el('div', 'browser-bar');
    const title = el('span', 'browser-title', '브라우저 화면을 불러오는 중…');
    const mk = (label, fn, cls) => { const b = el('button', 'btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; b.addEventListener('click', fn); return b; };
    const stage = el('div', 'browser-stage');
    const img = el('img', 'browser-shot');
    img.alt = 'Herdr 브라우저 화면 (눌러서 클릭)';
    stage.append(img);
    const ctl = el('div', 'browser-ctl');
    const field = el('input', 'browser-text');
    field.placeholder = '페이지에 입력할 글';
    field.enterKeyHint = 'send';
    let size = null;
    // Actions go out one at a time, in tap order; each finished action guarantees a fresh frame.
    let queue = Promise.resolve();
    let pending = 0;
    const busy = el('span', 'browser-busy', '');
    const act = (body) => {
      pending += 1;
      busy.textContent = '보내는 중 ' + pending;
      queue = queue.then(async () => {
        try {
          await api(base, { method: 'POST', body });
        } catch (e) { toast(e.message); }
        pending -= 1;
        busy.textContent = pending ? '보내는 중 ' + pending : '';
        setTimeout(load, 250);
      });
      return queue;
    };
    const sendText = async () => {
      const t = field.value;
      if (!t) return act({ action: 'key', key: 'Enter' });
      field.value = '';
      await act({ action: 'type', text: t });
    };
    field.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); sendText(); } });
    // 전체 보기(프레임 전체를 칸에 맞춤) ↔ 2배 확대(칸 안에서 스크롤). 웹페이지 자체의 확대/축소와는 별개다.
    const zoomBtn = mk('확대', () => {
      const on = stage.classList.toggle('zoomed');
      zoomBtn.textContent = on ? '전체' : '확대';
    });
    ctl.append(field, mk('입력', sendText, 'primary'), zoomBtn, mk('⏎', () => act({ action: 'key', key: 'Enter' })),
      mk('⌫', () => act({ action: 'key', key: 'Backspace' })), mk('▲', () => act({ action: 'scroll', dy: -600 })),
      mk('▼', () => act({ action: 'scroll', dy: 600 })), mk('←', () => act({ action: 'back' })));
    bar.append(title, busy, mk('새로 고침', () => load()));
    box.append(bar, stage, ctl);
    $('msg-list').append(box);
    // Touch: a short press without much movement is a tap (click there); a drag is a scroll of whatever
    // scrolls under the finger in the page. In 2x zoom the stage itself scrolls natively, so drags stay local.
    const toPage = (cx, cy) => {
      const r = img.getBoundingClientRect();
      return [((cx - r.left) / r.width) * size[0], ((cy - r.top) / r.height) * size[1], size[1] / r.height];
    };
    const mark = (cx, cy) => {
      const r = stage.getBoundingClientRect();
      const dot = el('span', 'tap-dot');
      dot.style.left = (cx - r.left + stage.scrollLeft) + 'px';
      dot.style.top = (cy - r.top + stage.scrollTop) + 'px';
      stage.append(dot);
      setTimeout(() => dot.remove(), 700);
    };
    let down = null;
    img.addEventListener('pointerdown', (e) => {
      down = { x: e.clientX, y: e.clientY, t: Date.now(), id: e.pointerId, sx: stage.scrollLeft, sy: stage.scrollTop };
    });
    img.addEventListener('pointercancel', () => { down = null; }); // the browser took it as a native scroll (zoom mode)
    img.addEventListener('pointerup', (e) => {
      const d = down;
      down = null;
      if (!d || d.id !== e.pointerId || !size) return;
      const dx = e.clientX - d.x, dy = e.clientY - d.y;
      const nativeScrolled = stage.scrollLeft !== d.sx || stage.scrollTop !== d.sy;
      if (Math.hypot(dx, dy) < 12 && Date.now() - d.t < 800 && !nativeScrolled) {
        const [x, y] = toPage(e.clientX, e.clientY);
        mark(e.clientX, e.clientY);
        act({ action: 'click', x, y });
      } else if (!stage.classList.contains('zoomed') && Math.abs(dy) > Math.abs(dx) && Math.abs(dy) >= 12) {
        const [x, y, k] = toPage(d.x, d.y);
        act({ action: 'scroll', dy: -dy * k, x, y }); // finger up = content moves up = scroll down
      }
    });
    img.addEventListener('dragstart', (e) => e.preventDefault());
    let loading = false;
    let again = false;
    async function load() {
      if (state.current !== id || state.source !== 'browser') { clearInterval(browserTimer); return; }
      if (loading) { again = true; return; } // an action finished mid-load: fetch once more right after
      loading = true;
      // Ask the page to lay out at this view's size (CSS px), so it fills the pane instead of a wide strip.
      const w = Math.round(stage.clientWidth), h = Math.round(Math.max(320, stage.clientHeight));
      try {
        const res = await fetch(base + '?w=' + w + '&h=' + h,
          { headers: { Authorization: 'Bearer ' + state.token }, cache: 'no-store' });
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).message || '화면을 받지 못했습니다');
        const blob = await res.blob();
        const vs = (res.headers.get('X-View-Size') || '').split(',').map(Number);
        const frameSize = vs[0] > 0 && vs[1] > 0 ? vs : [w, h]; // page CSS px of THIS frame
        const old = img.src;
        // Switch the click mapping only when this frame is actually on screen (no race with a slower load).
        img.onload = () => { size = frameSize; if (old) URL.revokeObjectURL(old); };
        img.src = URL.createObjectURL(blob);
        title.textContent = decodeURIComponent(res.headers.get('X-Page-Title') || '') || decodeURIComponent(res.headers.get('X-Page-Url') || '');
      } catch (e) {
        title.textContent = e.message;
      } finally {
        loading = false;
        if (again) { again = false; setTimeout(load, 0); }
      }
    }
    clearInterval(browserTimer);
    browserTimer = setInterval(() => { if (!document.hidden) load(); }, 3000);
    requestAnimationFrame(load);
  }

  // ---------- everyday Google Chrome: pick one tab, then the same browser view ----------
  // Only the tab the user picked (by target id) is shown; if it closes, nothing else is shown instead.
  function openChromePane(name, sub) {
    saveDraft();
    state.docId = null;
    state.fileFrom = null;
    $('doc').hidden = true;
    setScreenOpen(false);
    setFilesOpen(false);
    state.prompt = null;
    state.asking = false;
    document.body.classList.add('in-chat');
    $('empty').hidden = true;
    $('chat').hidden = false;
    $('blocked').hidden = true;
    $('chat-name').textContent = name;
    $('chat-sub').textContent = sub;
    $('chat-agent').textContent = '🌐';
    $('mode-btn').hidden = true;
    $('chat-status').className = 'badge';
    $('chat-status').textContent = '';
    $('chat-state').textContent = '';
    $('suggest').hidden = true;
    resetMessages();
    scheduleList();
  }

  async function openChromeList() {
    state.current = 'chrome';
    openChromePane('Chrome 탭', 'iMac · 평소 쓰는 Chrome (로그인 상태 그대로)');
    $('composer').hidden = true;
    $('readonly-note').hidden = false;
    $('readonly-note').textContent = '볼 탭을 고르세요. 처음 연결할 때는 iMac Chrome 에 뜨는 \'허용\' 을 눌러야 합니다.';
    const box = el('div', 'chrome-list');
    box.append(el('p', 'source-note', 'Chrome 에 연결하는 중… (처음이면 iMac 에서 허용을 눌러 주세요. 최대 1분)'));
    $('msg-list').replaceChildren(box);
    try {
      const d = await api('/api/chrome/tabs');
      if (state.current !== 'chrome') return;
      box.replaceChildren();
      for (const t of d.tabs || []) {
        const a = el('a', 'file-row');
        a.href = '#chrome:' + enc(t.id);
        const main = el('span', 'file-main');
        main.append(el('span', 'file-name', t.title || t.url), el('span', 'file-rel', t.url));
        a.append(el('span', 'kind kind-link', 'TAB'), main);
        box.append(a);
      }
      if (!(d.tabs || []).length) box.append(el('div', 'list-msg', '열린 탭이 없습니다.'));
    } catch (e) {
      if (state.current === 'chrome') box.replaceChildren(el('div', 'list-msg', e.message));
    }
  }

  function openChromeTab(target) {
    state.current = 'chrome:' + target;
    openChromePane('Chrome 탭', 'iMac · 고른 탭만 보입니다');
    $('readonly-note').hidden = true;
    showBrowser('/api/chrome/' + enc(target) + '/browser');
  }

  // ---------- pick-one questions (Claude AskUserQuestion / Codex request_user_input) ----------
  // Drawn as a card. Only the latest unanswered Claude question is live; the server turns picks into keys.
  function askCard(m) {
    const card = el('div', 'ask-card');
    card.dataset.tool = m.ask.tool || '';
    card.append(el('div', 'ask-title', '질문이 왔습니다'));
    m.ask.questions.forEach((q, qi) => {
      const box = el('div', 'ask-q');
      box.dataset.q = qi;
      box.dataset.multi = q.multi ? '1' : '';
      const head = el('div', 'ask-head');
      if (q.header) head.append(el('span', 'ask-tag', q.header));
      head.append(el('span', null, q.question + (q.multi && !/여러/.test(q.question) ? ' (여러 개 선택)' : '')));
      box.append(head);
      q.options.forEach((o, oi) => {
        const b = el('button', 'ask-opt');
        b.type = 'button';
        b.dataset.o = oi + 1;
        b.setAttribute('aria-pressed', 'false');
        b.append(el('span', 'ask-mark', q.multi ? '☐' : String(oi + 1)));
        const txt = el('span', 'ask-txt');
        txt.append(el('span', 'ask-label', o.label));
        if (o.description && o.description !== o.label) txt.append(el('span', 'ask-desc', o.description));
        b.append(txt);
        box.append(b);
      });
      const other = el('button', 'ask-opt ask-other');
      other.type = 'button';
      other.dataset.o = 'text';
      other.setAttribute('aria-pressed', 'false');
      other.append(el('span', 'ask-mark', '✎'), el('span', 'ask-label', '직접 입력'));
      const input = el('textarea', 'ask-input');
      input.rows = 2;
      input.hidden = true;
      input.placeholder = '답을 적어 주세요';
      box.append(other, input);
      card.append(box);
    });
    const foot = el('div', 'ask-foot');
    const note = el('span', 'ask-note', '');
    const send = el('button', 'btn primary ask-send', '답 보내기');
    send.type = 'button';
    send.disabled = true;
    foot.append(note, send);
    card.append(foot);
    return card;
  }

  function askAnswers(card) {
    return [...card.querySelectorAll('.ask-q')].map((box) => {
      const input = box.querySelector('.ask-input');
      if (!input.hidden) return input.value.trim() ? { text: input.value.trim() } : null;
      const picks = [...box.querySelectorAll('.ask-opt[aria-pressed="true"]:not(.ask-other)')].map((b) => Number(b.dataset.o));
      return picks.length ? { choices: picks } : null;
    });
  }

  function askRefresh(card) {
    card.querySelector('.ask-send').disabled = card.classList.contains('done') || askAnswers(card).some((a) => !a);
  }

  // Live only for the newest unanswered question; older ones become read-only.
  function askActivate() {
    const cards = [...$('msg-list').querySelectorAll('.ask-card')];
    const lastMsg = [...$('msg-list').querySelectorAll('[data-id]')].filter((n) => !n.classList.contains('pending')).pop();
    for (const c of cards) {
      const live = state.asking && lastMsg && lastMsg.contains(c) && c.dataset.tool === 'claude';
      c.classList.toggle('done', !live);
      c.querySelectorAll('button, textarea').forEach((b) => { b.disabled = !live; });
      c.querySelector('.ask-note').textContent = live ? '' : state.asking && c.dataset.tool !== 'claude' && lastMsg && lastMsg.contains(c)
        ? '이 질문은 위 화면에서 답해 주세요.' : '';
      if (live) askRefresh(c);
    }
  }

  $('msg-list').addEventListener('click', async (e) => {
    const card = e.target.closest('.ask-card');
    if (!card || card.classList.contains('done')) return;
    const opt = e.target.closest('.ask-opt');
    if (opt) {
      const box = opt.closest('.ask-q');
      const multi = box.dataset.multi === '1';
      const input = box.querySelector('.ask-input');
      const on = opt.getAttribute('aria-pressed') !== 'true';
      if (opt.classList.contains('ask-other')) {
        box.querySelectorAll('.ask-opt').forEach((b) => b.setAttribute('aria-pressed', 'false'));
        opt.setAttribute('aria-pressed', String(on));
        input.hidden = !on;
        if (on) input.focus();
      } else {
        if (!multi) box.querySelectorAll('.ask-opt').forEach((b) => b.setAttribute('aria-pressed', 'false'));
        box.querySelector('.ask-other').setAttribute('aria-pressed', 'false');
        input.hidden = true;
        opt.setAttribute('aria-pressed', String(multi ? on : true));
        if (multi) opt.querySelector('.ask-mark').textContent = on ? '☑' : '☐';
      }
      askRefresh(card);
      return;
    }
    const send = e.target.closest('.ask-send');
    if (!send || send.disabled) return;
    const id = state.current;
    send.disabled = true;
    send.textContent = '보내는 중…';
    try {
      await api(tpath(id, 'answer'), { method: 'POST', body: { answers: askAnswers(card) } });
      card.classList.add('done');
      card.querySelectorAll('button, textarea').forEach((b) => { b.disabled = true; });
      send.textContent = '보냈습니다';
      setTimeout(() => { if (state.current === id) loadLatest(false); }, 2000);
    } catch (err) {
      send.disabled = false;
      send.textContent = '답 보내기';
      toast(err.message || '보내지 못했습니다');
    }
  });
  $('msg-list').addEventListener('input', (e) => {
    const card = e.target.closest('.ask-card');
    if (card && e.target.matches('.ask-input')) askRefresh(card);
  });

  // ---------- sent-but-not-yet-recorded messages ----------
  // Shown dimmed right away; removed once the transcript has the same user text.
  const norm = (t) => String(t || '').replace(/\s+/g, ' ').trim();

  function addPending(text) {
    const wrap = renderMessage({ id: 'pending-' + Date.now(), role: 'user', text, queued: true });
    wrap.querySelector('.meta').textContent = '보내는 중…';
    $('msg-list').append(wrap);
    $('messages').scrollTop = $('messages').scrollHeight;
    return wrap;
  }

  // A server 'queued' copy replaces our optimistic bubble; the recorded message replaces either.
  function settlePending(list) {
    for (const m of list) {
      if (m.role !== 'user') continue;
      const t = norm(m.text);
      const sel = m.queued ? '.msg.pending[data-id^="pending-"]' : '.msg.pending';
      const p = [...$('msg-list').querySelectorAll(sel)].find((x) => x.dataset.text === t);
      if (p) p.remove();
    }
  }

  function fragOf(list) {
    const f = document.createDocumentFragment();
    for (const m of list) { state.seen.add(m.id); f.append(renderMessage(m)); }
    return f;
  }

  function updateOlder() {
    $('older').textContent = state.source !== 'transcript' ? ''
      : state.loadingOlder ? '이전 대화 불러오는 중…'
      : state.cursor ? '위로 올리면 이전 대화를 더 불러옵니다' : '대화의 처음입니다';
  }

  async function loadLatest(reset) {
    const id = state.current;
    if (!id) return;
    const cached = reset && cache[id] && cache[id].messages;
    if (cached) { applyMessages(cached, true); setSyncing(true); }
    let data;
    try {
      data = await api(tpath(id, 'messages?limit=' + LIMIT));
    } catch (e) {
      if (state.current === id) { setSyncing(false); toast('대화를 불러오지 못했습니다: ' + e.message); }
      return;
    }
    cachePut(id, { messages: data });
    if (state.current !== id) return;
    setSyncing(false);
    applyMessages(data, reset && !cached);
    loadSuggest();
  }

  function applyMessages(data, reset) {
    const box = $('messages');
    // An unanswered pick-one question (Claude AskUserQuestion / Codex request_user_input): open the answer panel.
    const lastAsk = data.asking ? ((data.messages || []).slice(-1)[0] || {}).ask : null;
    const tool = lastAsk && typeof lastAsk === 'object' ? lastAsk.tool : null;
    if (!!data.asking !== !!state.asking || tool !== state.askTool) {
      state.asking = !!data.asking;
      state.askTool = tool;
      updateBlocked();
    }
    if (data.source === 'browser') { showBrowser(); return; }
    if (data.source === 'screen') {
      const m = (data.messages || [])[0] || {};
      const note = el('p', 'source-note', '대화 기록과 연결되지 않아 터미널 화면 글자를 보여 줍니다.');
      const pre = el('pre', 'term', m.text || '(빈 화면)');
      resetMessages();
      state.source = 'screen';
      $('msg-list').append(note, pre);
      return;
    }
    const list = chronological(data.messages || []);
    const stick = reset || state.source !== 'transcript' || nearBottom(box);
    // Whole page is new: we missed messages in between (or the cache is old), so start over.
    // No overlap at all also covers a terminal re-paired to another transcript (cache held the old one).
    const gap = state.seen.size && list.length && list.every((m) => !state.seen.has(m.id));
    if (reset || gap || state.source !== 'transcript') {
      resetMessages();
      state.cursor = data.cursor;
    }
    state.source = 'transcript';
    // Server queue entries (queue-*) only live on the latest page: drop them and redraw from this page.
    for (const q of $('msg-list').querySelectorAll('.msg.pending:not([data-id^="pending-"])')) {
      state.seen.delete(q.dataset.id);
      q.remove();
    }
    const fresh = list.filter((m) => !state.seen.has(m.id));
    settlePending(fresh);
    $('msg-list').insertBefore(fragOf(fresh), $('msg-list').querySelector('.msg.pending'));
    linkifyIn($('msg-list'));
    askActivate();
    if (stick) box.scrollTop = box.scrollHeight;
    else if (!reset) state.unseen += fresh.filter((m) => m.role !== 'tool').length;
    updateJump();
    updateOlder();
    if (box.scrollHeight <= box.clientHeight + 400) loadOlder();
  }

  async function loadOlder() {
    const id = state.current;
    if (!id || !state.cursor || state.loadingOlder || state.source !== 'transcript') return;
    state.loadingOlder = true;
    updateOlder();
    try {
      const data = await api(tpath(id, 'messages?limit=' + LIMIT + '&before=' + enc(state.cursor)));
      if (state.current !== id) return;
      const box = $('messages');
      const prevH = box.scrollHeight;
      const list = chronological(data.messages || []).filter((m) => !state.seen.has(m.id));
      $('msg-list').prepend(fragOf(list));
      linkifyIn($('msg-list'));
      state.cursor = data.cursor;
      box.scrollTop += box.scrollHeight - prevH;
    } catch (e) {
      toast('이전 대화를 불러오지 못했습니다: ' + e.message);
    } finally {
      state.loadingOlder = false;
      updateOlder();
    }
  }

  $('messages').addEventListener('scroll', () => {
    if ($('messages').scrollTop < 400) loadOlder();
    updateJump();
  }, { passive: true });

  function updateJump() {
    const atBottom = nearBottom($('messages'));
    if (atBottom) state.unseen = 0;
    $('jump').hidden = atBottom;
    $('jump-count').hidden = !state.unseen;
    $('jump-count').textContent = state.unseen > 99 ? '99+' : state.unseen;
  }

  $('jump').addEventListener('click', () => {
    const box = $('messages');
    box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
  });

  // ---------- screen & blocked ----------

  // hc read takes 2-3 s on the server, so never stack requests for the same pane.
  async function refreshScreen(pre) {
    const id = state.current;
    if (!id || pre.dataset.loading === id) return;
    pre.dataset.loading = id;
    if (pre.dataset.for !== id) {
      pre.dataset.for = id;
      pre.textContent = (cache[id] && cache[id].screen) || '화면을 불러오는 중…';
    }
    try {
      const d = await api(tpath(id, 'screen?lines=60'));
      cachePut(id, { screen: d.text || '' });
      if (state.current !== id) return;
      pre.textContent = d.text || '(빈 화면)';
      pre.scrollTop = pre.scrollHeight;
    } catch (e) {
      if (state.current === id) pre.textContent = '화면을 불러오지 못했습니다: ' + e.message;
    } finally {
      delete pre.dataset.loading;
    }
  }

  function updateBlocked() {
    const t = cur();
    // A Claude question is answered from its card in the chat, so the raw screen panel stays closed.
    const claudeAsk = state.asking && state.askTool === 'claude';
    const on = !!t && !claudeAsk && (t.status === 'blocked' || state.asking || !!state.prompt);
    $('blocked').hidden = !on;
    if (!on) return;
    const p = state.prompt;
    $('quick').hidden = !t.writable || !!p;
    $('blocked-screen').hidden = !!p;
    $('prompt-card').hidden = !p;
    if (p) renderPrompt(p, t);
    else refreshScreen($('blocked-screen'));
  }

  // ---------- approval prompts (Claude 'Do you want to proceed?', Codex 'Would you like to run …?') ----------
  // The server reads the numbered choices off the screen; a tap presses that number.
  let promptSeq = 0;
  async function loadPrompt() {
    const id = state.current;
    const t = cur();
    const seq = ++promptSeq;
    if (!t || !t.writable || (t.status && t.status !== 'blocked')) {
      if (state.prompt) { state.prompt = null; updateBlocked(); }
      return;
    }
    try {
      const d = await api(tpath(id, 'prompt'));
      if (seq !== promptSeq || state.current !== id) return;
      const next = d && d.prompt;
      if (JSON.stringify(next) !== JSON.stringify(state.prompt)) { state.prompt = next || null; updateBlocked(); }
    } catch { /* keep the last view */ }
  }

  function renderPrompt(p, t) {
    const box = $('prompt-card');
    box.replaceChildren();
    box.append(el('div', 'prompt-q', p.question || '진행할까요?'));
    if (p.detail) box.append(el('pre', 'prompt-detail', p.detail));
    const opts = el('div', 'prompt-opts');
    for (const o of p.options) {
      const b = el('button', 'btn prompt-opt' + (/^(no|아니)/i.test(o.label) ? ' no' : o.n === 1 ? ' primary' : ''), o.label);
      b.type = 'button';
      b.dataset.n = o.n;
      b.disabled = !t.writable;
      opts.append(b);
    }
    box.append(opts);
  }

  $('prompt-card').addEventListener('click', async (e) => {
    const b = e.target.closest('.prompt-opt');
    if (!b || b.disabled) return;
    const id = state.current;
    $('prompt-card').querySelectorAll('button').forEach((x) => { x.disabled = true; });
    if (await post(tpath(id, 'keys'), { keys: [b.dataset.n] })) {
      toast('보냈습니다');
      state.prompt = null;
      updateBlocked();
      setTimeout(() => { if (state.current === id) { loadPrompt(); loadLatest(false); } }, 1500);
    } else {
      $('prompt-card').querySelectorAll('button').forEach((x) => { x.disabled = false; });
    }
  });
  // Some sessions never report 'blocked' (a Claude herdr sees as a shell), so peek while the chat is open.
  setInterval(() => { if (state.current && !document.hidden && !state.asking) loadPrompt(); }, 6000);

  function setScreenOpen(on) {
    if (on && state.filesOpen) setFilesOpen(false);
    state.screenOpen = on;
    $('screen-pane').hidden = !on;
    $('messages').hidden = on;
    const tg = $('screen-toggle');
    tg.setAttribute('aria-pressed', String(on));
    tg.setAttribute('aria-label', on ? '대화 보기' : '화면 보기');
    tg.querySelector('.lbl').textContent = on ? '대화 보기' : '화면 보기';
    tg.querySelector('.ico').textContent = on ? '💬' : '🖥️';
    if (on) refreshScreen($('screen-text'));
    else requestAnimationFrame(() => { // iPad: make WebKit re-measure after the wide terminal view is gone
      const app = document.querySelector('.app');
      app.style.display = 'none';
      void app.offsetHeight;
      app.style.display = '';
    });
  }

  $('screen-toggle').addEventListener('click', () => setScreenOpen(!state.screenOpen));
  $('screen-refresh').addEventListener('click', () => refreshScreen($('screen-text')));
  $('back').addEventListener('click', () => { location.hash = ''; });

  $('quick').addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    const id = state.current;
    if (!b || !id) return;
    let ok;
    if (b.dataset.action === 'interrupt') {
      if (!(await ask('지금 하던 작업을 중지할까요?', '중지'))) return;
      ok = await post(tpath(id, 'interrupt'));
    } else {
      b.disabled = true;
      setTimeout(() => { b.disabled = false; }, 500);
      ok = await post(tpath(id, 'keys'), { keys: [b.dataset.key] });
    }
    if (ok) {
      toast('보냄: ' + b.textContent);
      setTimeout(() => { if (state.current === id) updateBlocked(); }, 700);
    }
  });

  // ---------- composer ----------

  function autosize() {
    const ta = $('input');
    ta.style.height = 'auto';
    if (ta.scrollHeight) ta.style.height = ta.scrollHeight + 2 + 'px'; // 0 while the chat is hidden
  }
  $('input').addEventListener('input', autosize);

  // ---------- attachments ----------
  // Uploaded as soon as picked; the message gets the server paths appended when sent.

  const ATTACH_MAX = 5;
  const ATTACH_BYTES = 8 * 1024 * 1024;
  const SHRINK_OVER = 4 * 1024 * 1024;
  let attachSeq = 0;

  const chipsOf = (id) => state.attach.get(id) || [];

  function renderChips() {
    const items = chipsOf(state.current);
    const frag = document.createDocumentFragment();
    const label = { uploading: '올리는 중…', done: '완료', error: '실패', toobig: '너무 큼' };
    for (const it of items) {
      const c = el('div', 'chip-file ' + it.status);
      c.title = it.msg || it.name;
      const x = el('button', 'chip-x', '✕');
      x.type = 'button';
      x.dataset.key = it.key;
      x.setAttribute('aria-label', it.name + ' 첨부 빼기');
      c.append(el('span', 'chip-name', it.name), el('span', 'chip-state', fmtSize(it.size) + ' · ' + label[it.status]), x);
      frag.append(c);
    }
    $('chips').replaceChildren(frag);
    $('chips').hidden = !items.length;
    updateSend();
  }

  function updateSend() {
    const uploading = chipsOf(state.current).some((it) => it.status === 'uploading');
    $('send').disabled = state.sending || uploading;
  }

  $('chips').addEventListener('click', (e) => {
    const x = e.target.closest('.chip-x');
    if (!x) return;
    const items = chipsOf(state.current);
    const it = items.find((i) => String(i.key) === x.dataset.key);
    if (it) it.removed = true; // an upload in flight finishes but is ignored
    state.attach.set(state.current, items.filter((i) => i !== it));
    renderChips();
  });

  $('attach-btn').addEventListener('click', () => $('attach-input').click());
  $('attach-input').addEventListener('change', () => {
    addFiles([...$('attach-input').files]);
    $('attach-input').value = '';
  });
  $('input').addEventListener('paste', (e) => {
    const files = [...((e.clipboardData && e.clipboardData.files) || [])];
    if (!files.length) return;
    e.preventDefault();
    addFiles(files);
  });

  async function addFiles(files) {
    const id = state.current;
    if (!id || !files.length) return;
    const items = chipsOf(id);
    const room = ATTACH_MAX - items.length;
    if (files.length > room) toast('한 번에 ' + ATTACH_MAX + '개까지 첨부할 수 있습니다.');
    const picked = files.slice(0, Math.max(0, room)).map((f) => ({
      file: f, key: ++attachSeq, name: f.name || 'paste-' + attachSeq + '.png', size: f.size, status: 'uploading' }));
    state.attach.set(id, items.concat(picked));
    renderChips();
    for (const it of picked) await upload(id, it); // one at a time keeps memory low on phones
  }

  async function upload(id, it) {
    try {
      let blob = it.file;
      if (needsShrink(blob, it.name)) {
        const jpg = await shrinkImage(blob).catch(() => null);
        if (jpg) { blob = jpg; it.name = it.name.replace(/\.[^.]*$/, '') + '.jpg'; }
      }
      it.size = blob.size;
      if (blob.size > ATTACH_BYTES) { it.status = 'toobig'; return; }
      const data = await toBase64(blob);
      if (it.removed) return;
      const r = await api(tpath(id, 'attach'), { method: 'POST', body: { name: it.name, data } });
      // hc may rename (Korean, spaces, brackets -> '_'): keep showing the original name
      Object.assign(it, { status: 'done', path: r.path, size: r.size != null ? r.size : it.size });
    } catch (e) {
      it.status = 'error';
      it.msg = e.message;
    } finally {
      delete it.file;
      if (state.current === id) renderChips();
    }
  }

  // Claude can't read HEIC, and phone photos are often huge: re-encode as JPEG, long side 2048.
  function needsShrink(file, name) {
    const heic = /^image\/hei[cf]/i.test(file.type) || /\.hei[cf]$/i.test(name);
    return heic || (/^image\/(jpeg|png|webp)/i.test(file.type) && file.size > SHRINK_OVER);
  }

  async function shrinkImage(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      const scale = Math.min(1, 2048 / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.round(img.naturalWidth * scale);
      c.height = Math.round(img.naturalHeight * scale);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.85));
      if (!blob) throw new Error('encode failed');
      return blob;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function toBase64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).slice(String(r.result).indexOf(',') + 1));
      r.onerror = () => reject(r.error);
      r.readAsDataURL(blob);
    });
  }

  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = state.current;
    const text = $('input').value;
    const items = chipsOf(id);
    const paths = items.filter((it) => it.status === 'done').map((it) => it.name + ' → ' + it.path);
    if (!id || state.sending || (!text.trim() && !paths.length)) return;
    if (items.some((it) => it.status === 'uploading')) return;
    const failed = items.length - paths.length;
    if (failed && !(await ask('올리지 못한 첨부 ' + failed + '개는 빼고 보낼까요?', '보내기'))) return;
    if (text.length > 300 && !(await ask(text.length + '자를 보냅니다. 보낼까요?', '보내기'))) return;
    const body = paths.length ? (text.trim() ? text + '\n\n' : '') + '첨부 파일:\n' + paths.map((p) => '- ' + p).join('\n') : text;
    // server waits until the terminal is idle for typing; this can take up to ~40 s
    state.sending = true;
    updateSend();
    $('send').textContent = '보내는 중…';
    const pending = addPending(body);
    const ok = await post(tpath(id, 'send'), { text: body });
    state.sending = false;
    $('send').textContent = '보내기';
    updateSend();
    if (!ok) { pending.remove(); return; }
    const meta = pending.querySelector('.meta');
    if (pending.isConnected) meta.textContent = '대기 중';
    state.attach.delete(id);
    if (state.current === id) { $('input').value = ''; autosize(); renderChips(); }
    state.drafts.delete(id);
    toast('보냈습니다');
    setTimeout(() => { if (state.current === id) loadLatest(false); }, 800);
  });

  // ---------- live events ----------

  function onEvent(type, d) {
    if (!d || !d.id) return;
    if (type === 'terminal') {
      const t = state.terminals.get(d.id);
      if (!t) { loadTerminals().catch(() => {}); return; }
      Object.assign(t, d);
      scheduleList();
      if (d.id === state.current) {
        renderHeader();
        if (d.status && d.status !== 'working') setTimeout(loadSuggest, 1500); else $('suggest').hidden = true;
        loadPrompt();
        if (state.screenOpen) refreshScreen($('screen-text'));
      }
    } else if (type === 'message') {
      if (cache[d.id]) cache[d.id].dirty = true;
      schedulePrefetch();
      if (d.id === state.current) {
        loadLatest(false);
        clearTimeout(onEvent.files);
        onEvent.files = setTimeout(loadFiles, 1500);
        if (state.screenOpen) refreshScreen($('screen-text'));
      }
    } else if (type === 'gone') {
      state.terminals.delete(d.id);
      scheduleList();
      if (d.id === state.current) markGone();
    }
  }

  function setConn(on) {
    $('conn').className = 'conn' + (on ? ' on' : '');
    $('conn').textContent = on ? '실시간' : '다시 연결 중…';
  }

  function stopEvents() {
    if (state.es) { state.es.close(); state.es = null; }
  }

  function connectEvents() {
    if (MOCK) { setConn(true); return; }
    stopEvents();
    const es = new EventSource('/api/events');
    state.es = es;
    let opened = false;
    es.addEventListener('open', () => {
      if (opened || state.retry > 1000) refresh(); // reconnected: catch up on what we missed
      opened = true;
      state.retry = 1000;
      setConn(true);
    });
    es.addEventListener('error', () => {
      setConn(false);
      if (es.readyState === EventSource.CLOSED && state.es === es) {
        setTimeout(() => { if (state.es === es) connectEvents(); }, state.retry);
        state.retry = Math.min(state.retry * 2, 30000);
      }
    });
    for (const type of ['terminal', 'message', 'gone']) {
      es.addEventListener(type, (e) => { try { onEvent(type, JSON.parse(e.data)); } catch { /* ignore bad frame */ } });
    }
  }

  function refresh() {
    loadTerminals().catch(() => {});
    if (state.current) loadLatest(false);
    if (state.docId) loadDoc();
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden || !state.booted) return;
    refresh();
    if (!MOCK && (!state.es || state.es.readyState === EventSource.CLOSED)) connectEvents();
  });

  // ---------- push notifications ----------

  const PUSH_KEY = 'hc_push';
  const pushPrefs = () => Object.assign({ on: false, blocked: true, done: false }, JSON.parse(localStorage.getItem(PUSH_KEY) || '{}'));
  const isIos = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;

  function pushSheet(msg) {
    const p = pushPrefs();
    $('push-blocked').checked = p.blocked;
    $('push-done').checked = p.done;
    let note = msg || (p.on ? '알림이 켜져 있습니다.' : '알림이 꺼져 있습니다.');
    let can = true;
    if (!MOCK && isIos && !standalone) { note = 'iPhone·iPad 는 홈 화면 앱에서만 알림을 받을 수 있습니다. 공유 → 홈 화면에 추가 후 그 앱에서 켜세요.'; can = false; }
    else if (!MOCK && !('PushManager' in window && 'Notification' in window)) { note = '이 브라우저는 알림을 지원하지 않습니다.'; can = false; }
    $('push-msg').textContent = note;
    $('push-on').hidden = !can || p.on;
    $('push-off').hidden = !p.on;
    $('push-controls').hidden = !can;
    $('push-btn').classList.toggle('on', p.on);
  }

  function savePush(patch) {
    localStorage.setItem(PUSH_KEY, JSON.stringify(Object.assign(pushPrefs(), patch)));
  }

  function urlB64ToBytes(b64) {
    const s = atob((b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(s, (c) => c.charCodeAt(0));
  }

  async function pushSubscribe(prefs) {
    if (MOCK) return api('/api/push/subscribe', { method: 'POST', body: { subscription: { endpoint: 'mock' }, prefs } });
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      const k = await api('/api/push/key');
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToBytes(k.public_key || k.key || k.publicKey) });
    }
    return api('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON(), prefs } });
  }

  $('push-btn').addEventListener('click', () => { pushSheet(); $('push-dlg').showModal(); });
  $('push-close').addEventListener('click', () => $('push-dlg').close());

  $('push-on').addEventListener('click', async () => {
    // iOS: the permission prompt must start directly inside this tap
    const perm = MOCK ? 'granted' : await Notification.requestPermission();
    if (perm !== 'granted') return pushSheet('알림 권한이 거부되었습니다. 설정 앱에서 허용해 주세요.');
    const prefs = { blocked: $('push-blocked').checked, done: $('push-done').checked };
    try {
      await pushSubscribe(prefs);
      savePush(Object.assign({ on: true }, prefs));
      pushSheet('알림을 켰습니다.');
    } catch (e) {
      pushSheet('알림을 켜지 못했습니다: ' + e.message);
    }
  });

  $('push-off').addEventListener('click', async () => {
    try {
      let endpoint = 'mock';
      if (!MOCK) {
        const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
        endpoint = sub && sub.endpoint;
        if (sub) await sub.unsubscribe();
      }
      await api('/api/push/unsubscribe', { method: 'POST', body: { endpoint } }).catch(() => {});
    } finally {
      savePush({ on: false });
      pushSheet('알림을 껐습니다.');
    }
  });

  for (const box of ['push-blocked', 'push-done']) {
    $(box).addEventListener('change', async () => {
      const prefs = { blocked: $('push-blocked').checked, done: $('push-done').checked };
      savePush(prefs);
      if (pushPrefs().on) await pushSubscribe(prefs).catch((e) => pushSheet('저장하지 못했습니다: ' + e.message));
    });
  }
  pushSheet();

  // ---------- boot ----------

  async function boot() {
    await loadTerminals();
    state.booted = true;
    connectEvents();
    route();
  }

  function showBootError(e) {
    const box = el('div', 'list-msg', e.message);
    const retry = el('button', 'btn', '다시 시도');
    retry.type = 'button';
    retry.addEventListener('click', () => boot().catch(showBootError));
    box.append(el('br'), retry);
    $('list').replaceChildren(box);
  }

  // iOS keeps the layout viewport when the keyboard opens; follow the visual one instead.
  if (window.visualViewport) {
    const vv = window.visualViewport;
    const fullH = () => (matchMedia('(orientation: landscape)').matches ? Math.min : Math.max)(screen.width, screen.height);
    const fit = () => {
      const r = document.documentElement;
      // iOS bug (WebKit 301994): the home-screen app's page stops above the home indicator, so the
      // safe-area padding under the composer is wasted space there.
      r.classList.toggle('short', !!navigator.standalone && fullH() - window.innerHeight > 4);
      // Only follow the visual viewport while the keyboard is up; otherwise top/bottom: 0 fills the screen
      // (using vv.height always left a dead band under the composer on iPhone).
      // Also require a focused text field: iPadOS can leave the visual viewport shrunk after a dialog's
      // keyboard closes, which pinned the app to a band in the middle of the screen.
      const typing = document.activeElement && document.activeElement.matches('input, textarea, select, [contenteditable]');
      const kb = typing && window.innerHeight - vv.height > 120;
      r.classList.toggle('kb', kb);
      if (kb) r.style.setProperty('--vvh', vv.height + 'px'); else r.style.removeProperty('--vvh');
      r.style.setProperty('--vvt', kb ? vv.offsetTop + 'px' : '0px');
      if (kb && document.activeElement === $('input')) $('messages').scrollTop = $('messages').scrollHeight;
    };
    // iOS home-screen apps keep the shrunken viewport after the keyboard closes (black band at the
    // bottom for the rest of the session). Hiding and re-showing the full-height root makes WebKit
    // re-measure. ponytail: WebKit workaround, drop once iOS fixes it.
    const remeasure = () => {
      if (!navigator.standalone || fullH() - window.innerHeight <= 4) return;
      const app = document.querySelector('.app'), m = $('messages'), pos = m.scrollTop;
      app.style.display = 'none';
      void app.offsetHeight;
      app.style.display = '';
      m.scrollTop = pos;
      fit();
    };
    document.addEventListener('focusout', () => setTimeout(() => { fit(); remeasure(); }, 150));
    $('create-dlg').addEventListener('close', () => setTimeout(fit, 150));
    document.addEventListener('visibilitychange', () => { if (!document.hidden) setTimeout(remeasure, 150); });
    vv.addEventListener('resize', fit);
    vv.addEventListener('scroll', fit);
    fit();
  }

  window.addEventListener('hashchange', route);
  if ('serviceWorker' in navigator) {
    const hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.register('sw.js').then((reg) => {
      document.addEventListener('visibilitychange', () => { if (!document.hidden) reg.update().catch(() => {}); });
    }).catch(() => {});
    // a new app version took over: reload, but not while something is being typed or attached
    let reloadWanted = false;
    const reloadIfIdle = () => {
      if (!reloadWanted) return;
      if (!$('input').value && !chipsOf(state.current).length) location.reload();
    };
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadController) return; // first install, nothing old on screen
      reloadWanted = true;
      reloadIfIdle();
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden) reloadIfIdle(); });
    navigator.serviceWorker.addEventListener('message', (e) => {
      if (e.data && e.data.open) location.hash = enc(e.data.open);
    });
  }

  // ---------- mock mode (?mock=1): same API shape, data from mock/*.json, events from timers ----------

  let mockDb = null;
  const clone = (o) => JSON.parse(JSON.stringify(o));

  function mockLoad() {
    if (!mockDb) mockDb = (async () => {
      const get = (n) => fetch('mock/' + n + '.json', { cache: 'no-store' }).then((r) => r.json());
      const [t, messages, screens, layout, docs, machines, files] = await Promise.all(['terminals', 'messages', 'screens', 'layout', 'docs', 'machines', 'files'].map(get));
      const db = { terminals: t.terminals, messages, screens, layout, docs, machines, files };
      if (params.has('mbdown')) { // MacBook asleep: unreachable, no layout, no terminals
        const mb = db.machines.machines.find((m) => m.id === 'macbook-pro');
        Object.assign(mb, { state: 'unreachable', why: 'ssh 시간 초과', since_ms: Date.now() - 7 * 60000 });
        db.layout.machines.find((m) => m.id === 'macbook-pro').spaces = null;
        db.terminals = db.terminals.filter((x) => x.machine !== 'macbook-pro');
      }
      // long conversation to exercise paging and scroll performance
      const long = (db.messages.t90 = db.messages.t90 || []);
      const base = Date.now() - 400 * 60000;
      for (let i = 0; i < 360; i++) {
        const ts_ms = base + i * 60000;
        if (i % 3 === 2) long.push({ id: 't90-' + i, role: 'tool', text: '', ts_ms, tool: { name: 'Read', summary: 'docs/card-' + i + '.md' } });
        else long.push({ id: 't90-' + i, role: i % 3 ? 'assistant' : 'user', ts_ms,
          text: i + '번째 가짜 메시지입니다.' + (i % 5 ? '' : '\n\n- 항목 하나\n- 항목 둘\n\n`code` 와 **굵게** 표시') });
      }
      let n = 0;
      setInterval(() => {
        const w = db.terminals.find((x) => x.id === 't8p');
        n++;
        db.messages.t8p.push({ id: 'tick-' + Date.now() + '-' + n, role: 'assistant', ts_ms: Date.now(), text: '진행 보고 ' + n + ': 가짜 작업을 계속하고 있습니다.' });
        w.turn++;
        mockStatus(w, 'working');
        onEvent('message', { id: 't8p' });
      }, 7000);
      return db;
    })();
    return mockDb;
  }

  function mockStatus(t, status) {
    t.status = status;
    t.status_ms = Date.now();
    onEvent('terminal', { id: t.id, status, turn: t.turn, status_ms: t.status_ms });
  }

  async function mockApi(path, method, body) {
    const db = await mockLoad();
    await new Promise((r) => setTimeout(r, 120));
    if (state.token === 'bad') { logout('토큰이 맞지 않습니다. 다시 입력해 주세요.'); throw new ApiError(401, 'unauthorized'); }
    const url = new URL(path, location.origin);
    if (url.pathname === '/api/terminals') return clone({ terminals: db.terminals });
    if (url.pathname === '/api/machines') return clone(db.machines);
    if (url.pathname === '/api/push/key') return { key: 'BMockKey' };
    if (url.pathname.startsWith('/api/push/')) { db.push = body; return { ok: true }; }
    if (url.pathname.endsWith('/files')) return clone({ files: db.files[decodeURIComponent(url.pathname.split('/')[3])] || [] });
    if (url.pathname === '/api/layout') {
      if (params.has('nolayout')) throw new ApiError(404, 'not_found', '없는 요청입니다.');
      return clone(db.layout);
    }
    if (url.pathname.startsWith('/api/docs/')) {
      const doc = db.docs[decodeURIComponent(url.pathname.slice(10))];
      if (!doc) throw new ApiError(404, 'not_found', '문서가 없습니다.');
      return clone(doc);
    }
    const [, , , rawId, action] = url.pathname.split('/');
    const id = decodeURIComponent(rawId || '');
    const t = db.terminals.find((x) => x.id === id);
    if (!t) throw new ApiError(404, 'not_found', '터미널이 없습니다.');
    const screen = db.screens[id] || '(가짜 화면) ' + id + '\n$ ';
    if (method === 'GET' && action === 'messages') {
      if (!t.chat) return { source: 'screen', cursor: null, messages: [{ id: 'screen', role: 'tool', text: screen, ts_ms: Date.now(), tool: { name: 'screen', summary: '' } }] };
      const all = db.messages[id] || [];
      const limit = Number(url.searchParams.get('limit')) || 50;
      const end = url.searchParams.has('before') ? Number(url.searchParams.get('before')) : all.length;
      const start = Math.max(0, end - limit);
      return clone({ source: 'transcript', messages: all.slice(start, end), cursor: start > 0 ? String(start) : null });
    }
    if (method === 'GET' && action === 'screen') return { text: screen, status: t.status };
    if (action === 'mode') {
      const modes = t.agent === 'claude-code' ? [['auto', '자동', '위험해 보이는 일만 묻고 나머지는 알아서 합니다.'], ['manual', '매번 확인', '파일 수정·명령 실행마다 묻습니다.'],
        ['accept-edits', '수정 자동 허용', '파일 수정은 묻지 않고, 명령 실행은 묻습니다.'], ['plan', '계획만', '아무것도 고치지 않고 계획만 세웁니다.'],
        ['bypass', '모든 권한', '아무것도 묻지 않습니다. 켜 두고 시작한 세션에서만 됩니다.']]
        : t.agent === 'codex' ? [['ask', '승인 요청', '작업 폴더 안은 알아서 하고, 인터넷·폴더 밖 수정은 묻습니다.'], ['full', '전체 권한', '폴더 밖 수정·인터넷도 묻지 않습니다.']] : [];
      if (method === 'POST') { await new Promise((r) => setTimeout(r, 1500)); t.mode = body.mode; return { ok: true, current: body.mode }; }
      return { modes: modes.map(([id, label, desc]) => ({ id, label, desc })), current: t.mode || (t.agent === 'claude-code' ? 'auto' : null) };
    }
    if (method === 'POST') {
      if (!t.writable) throw new ApiError(403, 'shell_blocked', '셸 터미널에는 보낼 수 없습니다.');
      const say = (role, text) => { (db.messages[id] = db.messages[id] || []).push({ id: 'm' + Date.now() + Math.random(), role, text, ts_ms: Date.now() }); onEvent('message', { id }); };
      if (action === 'send') {
        // busy terminal: the text waits in the input queue first (queued:true), like A's transcript reader
        const list = (db.messages[id] = db.messages[id] || []);
        const busy = t.status === 'working';
        const qid = 'queue-' + Date.now();
        if (busy) { list.push({ id: qid, role: 'user', text: body.text, ts_ms: Date.now(), queued: true }); onEvent('message', { id }); }
        setTimeout(() => {
          db.messages[id] = list.filter((m) => m.id !== qid);
          say('user', body.text);
          mockStatus(t, 'working');
          setTimeout(() => { say('assistant', '(가짜 응답) 받았습니다: ' + body.text.slice(0, 40)); mockStatus(t, 'done'); }, 1500);
        }, busy ? 2500 : 600);
      } else if (action === 'keys') {
        if (t.status === 'blocked') {
          mockStatus(t, 'working');
          setTimeout(() => { say('assistant', '선택(' + body.keys.join(',') + ')을 받아 계속 진행했습니다.'); mockStatus(t, 'done'); }, 2000);
        }
      } else if (action === 'attach') {
        await new Promise((r) => setTimeout(r, 400));
        const name = String(body.name).replace(/[^\w.\-]/g, '_'); // like hc
        return { ok: true, name, path: '/Users/me/.herdr-chat/uploads/' + id.replace('/', '_') + '/' + name, size: Math.floor(body.data.length * 3 / 4) };
      } else if (action === 'interrupt') {
        mockStatus(t, 'idle');
      } else throw new ApiError(404, 'not_found', '없는 요청입니다.');
      return { ok: true };
    }
    throw new ApiError(404, 'not_found', '없는 요청입니다.');
  }

  // start last: mock state above must be initialised first
  if (!state.token) showLogin();
  else boot().catch((e) => { if (e.status !== 401) showBootError(e); });
}
