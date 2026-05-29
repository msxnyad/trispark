/* Trispark Service Worker — offline-first app shell cache */
var CACHE = 'trispark-v1';
var SHELL = ['/', '/index.html', '/sortable.min.js'];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(c) { return c.addAll(SHELL); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k){ return k !== CACHE; }).map(function(k){ return caches.delete(k); }));
    }).then(function(){ return clients.claim(); })
  );
});

self.addEventListener('fetch', function(e) {
  var url = new URL(e.request.url);

  /* API calls — network first, 3s timeout, return {offline:true} on fail */
  if (url.pathname === '/status' || url.pathname === '/sync' ||
      url.pathname === '/reset-today' || url.pathname === '/reset-all' ||
      url.pathname === '/sync-time') {
    e.respondWith(
      Promise.race([
        fetch(e.request.clone()),
        new Promise(function(_, rej){ setTimeout(function(){ rej(new Error('sw-timeout')); }, 3000); })
      ]).catch(function() {
        return new Response(JSON.stringify({offline: true}), {
          status: 200,
          headers: {'Content-Type': 'application/json'}
        });
      })
    );
    return;
  }

  /* App shell — cache-first, update in background */
  e.respondWith(
    caches.match(e.request).then(function(cached) {
      var network = fetch(e.request).then(function(res) {
        if (res && res.status === 200 && e.request.method === 'GET') {
          var clone = res.clone();
          caches.open(CACHE).then(function(c){ c.put(e.request, clone); });
        }
        return res;
      }).catch(function(){});
      return cached || network;
    })
  );
});
