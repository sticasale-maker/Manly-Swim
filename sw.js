// ─────────────────────────────────────────────────────────────
//  SWIM MANLY — Service Worker
//  Strategy: network-first for app shell, offline fallback.
//  API calls (WillyWeather, Open-Meteo, Supabase, Beachwatch)
//  are never cached — always pass through to network.
//
//  To force all clients to update: bump CACHE_VERSION below.
// ─────────────────────────────────────────────────────────────

const CACHE_VERSION = '20260625-160805';
const CACHE_NAME    = 'swim-manly-' + CACHE_VERSION;

// App shell assets to pre-cache on install
const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/icon_small.png',
  '/icon_big_360.png',
  '/marco2.png',
  // Google Fonts — cached so the app looks right offline
  'https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300&family=Playfair+Display:ital,wght@0,700;1,400&display=swap',
];

// Hostnames whose requests should NEVER be cached (API traffic)
const PASSTHROUGH_HOSTS = [
  'bold-rain-6ded.sticasale.workers.dev',  // Cloudflare Worker
  'api.open-meteo.com',
  'marine-api.open-meteo.com',
  'gkspukabnfbzrvjoewpc.supabase.co',      // Supabase
  'docs.google.com',                        // Google Sheet CSV
  'fonts.gstatic.com',                      // font files — let browser cache naturally
];

// ── Install: pre-cache shell assets ──────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      // Cache what we can; don't let one failure block the install
      return Promise.allSettled(
        SHELL_ASSETS.map(url =>
          cache.add(url).catch(err => console.warn('[SW] pre-cache failed:', url, err))
        )
      );
    }).then(() => self.skipWaiting())
  );
});

// ── Message: allow the page to activate a waiting worker ──────
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  // Reply with the running cache version so the page can display it.
  if (event.data && event.data.type === 'GET_VERSION') {
    if (event.ports && event.ports[0]) {
      event.ports[0].postMessage({ version: CACHE_VERSION });
    }
  }
});

// ── Activate: delete old caches ───────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('swim-manly-') && k !== CACHE_NAME)
          .map(k => {
            console.log('[SW] deleting old cache:', k);
            return caches.delete(k);
          })
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for shell, passthrough for APIs ──────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Always pass through API calls — never cache
  if (PASSTHROUGH_HOSTS.some(h => url.hostname.includes(h))) {
    return; // let browser handle normally
  }

  // Only handle GET requests
  if (event.request.method !== 'GET') return;

  // Network-first: try network, update cache, fall back to cache
  event.respondWith(
    fetch(event.request)
      .then(networkResponse => {
        // Only cache valid same-origin or CORS responses
        if (
          networkResponse &&
          networkResponse.status === 200 &&
          (networkResponse.type === 'basic' || networkResponse.type === 'cors')
        ) {
          const toCache = networkResponse.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, toCache));
        }
        return networkResponse;
      })
      .catch(() => {
        // Network failed — serve from cache (offline fallback)
        return caches.match(event.request).then(cached => {
          if (cached) return cached;
          // If it's a navigation request and we have no cache, serve index.html
          if (event.request.mode === 'navigate') {
            return caches.match('/index.html');
          }
          return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
        });
      })
  );
});
