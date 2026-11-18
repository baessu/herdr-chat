// App shell only: network first (revalidated, never a stale HTTP-cache copy), cache as offline fallback.
// /api/ and mock/ are never cached. Bump CACHE when shipping so old shells are dropped.
const CACHE = 'herdr-chat-v32';
const SHELL = ['./', 'index.html', 'app.js', 'style.css', 'manifest.webmanifest', 'icon.svg', 'icon-180.png', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE)
    .then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.includes('/mock/')) return;
  const req = e.request.mode === 'navigate'
    ? new Request(url.href, { cache: 'no-cache', credentials: 'same-origin' })
    : new Request(e.request, { cache: 'no-cache' });
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return res;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});

// Payload from the server: {"title", "body", "tag", "id"} (id = terminal to open).
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'herdr', {
    body: d.body || '',
    tag: d.tag || d.id || undefined,
    icon: 'icon-192.png',
    data: { id: d.id || null },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const id = e.notification.data && e.notification.data.id;
  // The app routes a terminal as '#<encoded id>' (not '#t:…', which opened the list).
  const url = new URL(id ? './#' + encodeURIComponent(id) : './', self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (wins) => {
    const w = wins[0];
    if (!w) return self.clients.openWindow(url);
    const c = (await w.focus().catch(() => null)) || w;
    if (!id) return c;
    c.postMessage({ open: id });
    // iOS may drop a message posted to a suspended page; navigating is the reliable fallback.
    if ('navigate' in c && !c.url.endsWith('#' + encodeURIComponent(id))) return c.navigate(url).catch(() => c);
    return c;
  }));
});
