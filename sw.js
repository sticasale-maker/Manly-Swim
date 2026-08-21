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

const CACHE_VERSION = '20260821-192419';
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
  // Live cam stills. MUST be passthrough: these are captioned as current, and a
  // network-first SW would happily serve yesterday's frame from cache on a bad
  // connection — a stale photo presented as "right now" is the worst kind of
  // wrong this app can be. Never cache a frame that claims to be live.
  'camstills.cdn-surfline.com',
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

// ── Push: bluebottle alerts ───────────────────────────────────
// Payload-less by design — the sender (Cloudflare Worker) posts a VAPID-signed
// push with no encrypted body, so we show a fixed notification. If a body is
// ever attached later, we honour it.
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* no or non-JSON payload */ }
  const title = data.title || '🪼 Bluebottles at Manly';
  const body  = data.body  || 'A swimmer reported bluebottles with a photo. Tap to check the bay.';
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: './images/logos/icon-192.png',
      badge: './images/logos/favicon-32.png',
      tag: 'bluebottle',
      renotify: true,
      data: { url: './' }
    })
  );
});

// ── Notification click: focus an open tab or open the app ─────
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || './';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(cls => {
      for (const c of cls) {
        if ('focus' in c) {
          if (typeof c.navigate === 'function') { try { c.navigate(url); } catch (e) {} }
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
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

  // The splash video is the one media file worth caching: it is 2.3 MB, it is
  // byte-identical every time, and it is the first thing between a swimmer and
  // the number they opened the app for. It gets its own handler because the
  // generic rule below cannot serve it — see serveSplashVideo() for why a
  // network-first SW breaks <video>, and how the Range/206 contract is honoured.
  if (url.origin === self.location.origin &&
      /images\/logosplash\.mp4$/i.test(url.pathname)) {
    event.respondWith(serveSplashVideo(event.request, url));
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

// ── Splash video: cached, with the Range contract honoured by hand ──────────
// WHY THIS IS NOT JUST "delete the .mp4 exclusion":
// <video> does not fetch a file, it fetches byte ranges. Safari in particular
// issues `Range: bytes=0-` and REQUIRES a 206 with a correct Content-Range back;
// hand it the plain 200 that a naive cache.match() returns and playback stalls
// or dies. That is the exact failure the blanket media bail-out above was written
// to avoid. So to cache this file we have to serve the ranges ourselves.
//
// Cached under the VERSIONED cache deliberately: a CI deploy therefore re-fetches
// it once, which is the price of never serving a stale logo. That costs nothing
// in practice because index.html only attaches the src on the week's first
// launch — so the download happens at most once a week per device regardless.
async function serveSplashVideo(request, url) {
  // Key on origin+pathname so a cache-buster query can never fragment the entry.
  const key = url.origin + url.pathname;
  let cached;
  try {
    const cache = await caches.open(CACHE_NAME);
    cached = await cache.match(key);
    if (!cached) {
      const fresh = await fetch(key, { cache: 'reload' });
      if (!fresh || fresh.status !== 200) return fetch(request);
      // put() consumes the clone; `fresh` stays readable for us to serve from.
      await cache.put(key, fresh.clone());
      cached = fresh;
    }
  } catch (e) {
    return fetch(request);           // cache unavailable (private mode, quota) — just stream it
  }

  const range = request.headers.get('range');
  if (!range) return cached;         // no Range asked for: the plain 200 is correct

  let buf;
  try { buf = await cached.arrayBuffer(); }
  catch (e) { return fetch(request); }
  const total = buf.byteLength;

  const m = /bytes=(\d*)-(\d*)/i.exec(range);
  if (!m) return cached;

  let start, end;
  if (m[1] === '') {
    // Suffix form `bytes=-N` — the LAST n bytes, not the first n.
    const n = parseInt(m[2], 10);
    if (!isFinite(n) || n <= 0) return cached;
    start = Math.max(0, total - n);
    end   = total - 1;
  } else {
    start = parseInt(m[1], 10);
    end   = m[2] === '' ? total - 1 : parseInt(m[2], 10);
  }

  if (!isFinite(start) || !isFinite(end) || start > end || start >= total) {
    return new Response(null, {
      status: 416,
      headers: { 'Content-Range': 'bytes */' + total }
    });
  }
  end = Math.min(end, total - 1);

  return new Response(buf.slice(start, end + 1), {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type':   cached.headers.get('Content-Type') || 'video/mp4',
      'Content-Length': String(end - start + 1),
      'Content-Range':  'bytes ' + start + '-' + end + '/' + total,
      'Accept-Ranges':  'bytes'
    }
  });
}
