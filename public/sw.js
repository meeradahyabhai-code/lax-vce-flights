// DFC2026 service worker — offline support for a cruise-wifi audience.
//
// Goal (read-first use case): at sea and at ports the app must open and show the
// itinerary, bookings, port reference, and already-loaded ("pre-queried")
// restaurants without a connection. Live search / AI endpoints stay network-only.
//
// CRITICAL: the app shell is NETWORK-FIRST so daily Vercel deploys always reach
// users on next launch (the PWA auto-update model). Never make navigation
// cache-first. Bump VERSION to purge old caches on activate.
var VERSION = 'dfc-v1';
var SHELL_CACHE = VERSION + '-shell';
var DATA_CACHE = VERSION + '-data';
var ASSET_CACHE = VERSION + '-assets'; // images + audio (cache-on-view + bulk "Save for offline")

// Small, always-precached on install (shell + core trip data). Media is NOT here
// (23MB) — it caches on view, or all-at-once via the "Save trip for offline" button.
var PRECACHE_SHELL = ['/', '/index.html', '/manifest.json', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'];
var PRECACHE_DATA = ['/restaurants.json', '/excursions.json', '/flights.json', '/hotels_venice.json', '/port_climate.json', '/audio/manifest.json'];

var SUPA_HOST = 'vmpmkeisipzbzcrpssia.supabase.co';
// Live endpoints that need the network (OpenAI / SerpAPI) — never cached.
var LIVE_API = /\/api\/(summary|points|hotel-points|hotels|hotel_search|multicity|parse_screenshot|parse_hotel|parse_excursion)\b/;
// Read-only data endpoints worth caching for offline.
var DATA_API = /\/api\/(restaurants|flights)\b/;

function putLater(cacheName, req, res) {
  var copy = res.clone();
  caches.open(cacheName).then(function (c) { c.put(req, copy); });
}

function tolerantAddAll(cacheName, urls) {
  return caches.open(cacheName).then(function (c) {
    return Promise.all(urls.map(function (u) {
      return fetch(u, { cache: 'reload' })
        .then(function (r) { if (r && r.ok) return c.put(u, r.clone()); })
        .catch(function () {});
    }));
  });
}

self.addEventListener('install', function (event) {
  event.waitUntil(
    Promise.all([
      tolerantAddAll(SHELL_CACHE, PRECACHE_SHELL),
      tolerantAddAll(DATA_CACHE, PRECACHE_DATA),
    ]).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k.indexOf(VERSION) !== 0) return caches.delete(k); // purge old versions
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

// ---- fetch strategies ----
function navHandler(req) {
  // network-first: keeps deploys landing; falls back to the cached shell offline
  return fetch(req).then(function (r) {
    if (r && r.ok) putLater(SHELL_CACHE, '/', r);
    return r;
  }).catch(function () {
    return caches.match('/').then(function (c) { return c || caches.match('/index.html'); });
  });
}
function networkFirst(req, cacheName) {
  return fetch(req).then(function (r) {
    if (r && r.ok) putLater(cacheName, req, r);
    return r;
  }).catch(function () { return caches.match(req); });
}
function cacheFirst(req, cacheName) {
  return caches.match(req).then(function (hit) {
    if (hit) return hit;
    return fetch(req).then(function (r) {
      if (r && (r.ok || r.type === 'opaque')) putLater(cacheName, req, r);
      return r;
    });
  });
}
function staleWhileRevalidate(req, cacheName) {
  return caches.match(req).then(function (hit) {
    var net = fetch(req).then(function (r) {
      if (r && r.ok) putLater(cacheName, req, r);
      return r;
    }).catch(function () { return hit; });
    return hit || net; // cached instantly when present; refresh in background
  });
}
// Static JSON loaders append a ?v=<Date.now()> cache-buster, so match/store under
// the query-stripped path — otherwise the precached bare path never matches offline.
function staleWhileRevalidateJSON(req, cacheName) {
  var keyUrl = new URL(req.url); keyUrl.search = '';
  var key = keyUrl.toString();
  return caches.match(key).then(function (hit) {
    var net = fetch(req).then(function (r) {
      if (r && r.ok) putLater(cacheName, key, r); // stable key, no cache-buster
      return r;
    }).catch(function () { return hit; });
    return hit || net;
  });
}

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') return; // writes pass through; the app guards them offline
  var url;
  try { url = new URL(req.url); } catch (e) { return; }

  // Supabase reads → network-first with cached fallback (offline shows last sync)
  if (url.hostname === SUPA_HOST) { event.respondWith(networkFirst(req, DATA_CACHE)); return; }

  if (url.origin !== self.location.origin) return; // other cross-origin: leave alone

  if (LIVE_API.test(url.pathname)) return;          // live AI/search: network only
  if (req.mode === 'navigate') { event.respondWith(navHandler(req)); return; }

  if (/\.json$/.test(url.pathname)) {           // static JSON (cache-buster query stripped)
    event.respondWith(staleWhileRevalidateJSON(req, DATA_CACHE)); return;
  }
  if (DATA_API.test(url.pathname)) {            // /api/restaurants|flights — query is meaningful
    event.respondWith(staleWhileRevalidate(req, DATA_CACHE)); return;
  }
  if (url.pathname.indexOf('/media/') === 0 || url.pathname.indexOf('/audio/') === 0 ||
      /\.(png|jpg|jpeg|webp|svg|ico|mp4)$/.test(url.pathname) || url.pathname === '/manifest.json') {
    event.respondWith(cacheFirst(req, ASSET_CACHE)); return;
  }
  event.respondWith(networkFirst(req, ASSET_CACHE));
});

// ---- "Save trip for offline": bulk-precache a client-supplied URL list ----
function precacheUrls(urls, client) {
  var dataExt = /\.json$/;
  var done = 0, total = urls.length, i = 0, CONC = 6;
  function post(type) { if (client) client.postMessage({ type: type, done: done, total: total }); }
  function one() {
    if (i >= urls.length) return Promise.resolve();
    var u = urls[i++];
    var cacheName = dataExt.test(u.split('?')[0]) ? DATA_CACHE : ASSET_CACHE;
    return caches.open(cacheName).then(function (cache) {
      return cache.match(u).then(function (hit) {
        if (hit) return; // already saved
        return fetch(u, { cache: 'reload' })
          .then(function (r) { if (r && (r.ok || r.type === 'opaque')) return cache.put(u, r.clone()); })
          .catch(function () {});
      });
    }).then(function () { done++; post('PRECACHE_PROGRESS'); return one(); });
  }
  var runners = [];
  for (var k = 0; k < CONC; k++) runners.push(one());
  return Promise.all(runners).then(function () { post('PRECACHE_DONE'); });
}

self.addEventListener('message', function (e) {
  var d = e.data || {};
  if (d.type === 'PRECACHE' && Array.isArray(d.urls)) {
    if (e.waitUntil) e.waitUntil(precacheUrls(d.urls, e.source));
    else precacheUrls(d.urls, e.source);
  } else if (d.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
