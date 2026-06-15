// DFC2026 service worker.
// Minimal for now — its only job is to make the app installable (a registered
// SW with a fetch handler). Offline caching is added in the offline phase;
// keep this NETWORK-FIRST so deploys always show (never cache-first).
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* network passthrough — offline caching comes next phase */ });
