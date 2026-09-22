// Service worker: makes Level installable and keeps it usable on a bad signal.
// Pages are fetched fresh when there's a connection, and the last version is
// kept so a tradie in a basement still sees the job they just opened.
const VERSION = 'level-v2';
const SHELL = ['/static/style.css', '/static/app.js', '/static/icons/icon-192.png', '/offline'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;

  // Static files: serve from the cache, refresh in the background.
  if (new URL(request.url).pathname.startsWith('/static/')) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request).then((resp) => {
      const copy = resp.clone();
      caches.open(VERSION).then((cache) => cache.put(request, copy));
      return resp;
    })));
    return;
  }

  // Pages: try the network, fall back to the last copy, then the offline page.
  event.respondWith(
    fetch(request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(VERSION).then((cache) => cache.put(request, copy));
        return resp;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match('/offline')))
  );
});
