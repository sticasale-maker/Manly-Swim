// ─────────────────────────────────────────────────────────────
//  SWIM MANLY — Service Worker
//  Strategy: network-first for app shell, offline fallback.
//  API calls (WillyWeather, Open-Meteo, Supabase, Beachwatch)
//  are never cached — always pass through to network.
//
//  Paths are RELATIVE so they resolve against this worker's own
//  location (…/Manly-Swim/sw.js) — i.e. the app folder, not the
//  domain root. Register it from index.html with './sw.js'.
//
//  CACHE_VERSION is stamped automatically by CI — do not edit by hand.
// ─────────────────────────────────────────────────────────────

const CACHE_VERSION = '20260717-142748';
const CACHE_NAME    = 'swim-manly-' + CACHE_VERSION;

// App shell assets to pre-cache on install (relative to /Manly-Swim/)
const SHELL_ASSETS = [
  './',
  './index.html',
  './vecchio.html',
  './manifest.webmanifest',
  './images/logos/splash.png',
  './images/logos/icon-180.png',
  './images/logos/favicon-32.png',
  './images/marco2.png',
  // Google Fonts — must match the exact URL index.html requests, or it won't hit
  'https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Mono:ital,wght@0,300;0,400;0,500;1,300;1,500&family=Playfair+Display:ital,wght@0,700;1,400&display=swap',
];

// Hostnames whose requests should NEVER be cached (API traffic)
const PASSTHROUGH_HOSTS = [
  'middleman-to-sheet.sticasale.workers.dev', // sheet-proxy Worker (config/gem/nsdisp CSV) — never SW-cache
  'bold-rain-6ded.sticasale.workers.dev',  // Cloudflare Worker (API proxy)
  'api.open-meteo.com',
  'marine-api.open-meteo.com',
  'gkspukabnfbzrvjoewpc.supabase.co',      // Supabase
  'docs.google.com',                        // Google Sheet CSV (direct fallback)
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
     // Force any window still open on the stale fallback over to the current build.
     // index.html re-runs the NS preflight (bounce-guarded), so a genuine outage just
     // fails over again — this fires once per SW activation and can't loop.
     .then(() => self.clients.matchAll({ type: 'window', includeUncontrolled: true })
       .then(cls => Promise.all(cls.map(c =>
         (c.url && c.url.indexOf('vecchio.html') !== -1 && typeof c.navigate === 'function')
           ? c.navigate('index.html').catch(() => {})
           : null
       )))
       .catch(() => {}))
  );
});

// ── Fetch: network-first for shell, passthrough for APIs ──────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Ignore non-http(s) schemes (e.g. chrome-extension://) — they can't be cached
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;

  // Don't intercept media or range requests. A network-first SW mishandles the
  // HTTP Range / 206 responses that <video>/<audio> rely on, which stalls or
  // breaks playback (notably the splash video on iOS Safari). Let the browser
  // stream these natively.
  if (event.request.headers.has('range') ||
      /\.(mp4|webm|ogg|ogv|mov|m4v|m4a|mp3|wav|aac)$/i.test(url.pathname)) {
    return;
  }

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
          // If it's a navigation request and we have no cache, serve the app shell
          // that MATCHES the launched app. index.html is nuovo; vecchio.html is the
          // fallback app it redirects to when the NS feed or the NS dispersion table
          // is unavailable. An offline vecchio launch must not render nuovo, which
          // would immediately fail its preflight and bounce straight back here.
          if (event.request.mode === 'navigate') {
            const shell = url.pathname.includes('vecchio') ? './vecchio.html' : './index.html';
            return caches.match(shell).then(m => m || caches.match('./index.html'));
          }
          return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
        });
      })
  );
});