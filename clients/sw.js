// Hestia app shell service worker v5: network-first for pages (an HTML edit shows up on the
// next load, no cache bump needed), cache-first for icons, never touches dynamic endpoints.
const CACHE = 'hestia-shell-v5';
const PAGES = ['/app', '/clips'];
const STATIC = ['/manifest.json', '/icon.png', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll([...PAGES, ...STATIC])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
function stash(req, res) {
  if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
  return res;
}
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== self.location.origin) return;
  // Only exact shell paths are intercepted; every dynamic route goes straight to the network.
  if (PAGES.includes(u.pathname)) {
    e.respondWith(fetch(e.request).then((res) => stash(e.request, res))
      .catch(() => caches.match(u.pathname).then((hit) => hit || caches.match('/app'))));
  } else if (STATIC.includes(u.pathname)) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => stash(e.request, res))));
  }
});
