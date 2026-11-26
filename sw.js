/* Macro Forex : application installable.
   - La page et les données déjà vues s'ouvrent tout de suite depuis la mémoire de l'appareil,
     puis sont mises à jour en arrière-plan (la page est prévenue quand il y a du neuf).
   - Cours en direct, vidéos, TradingView, Cloudflare : jamais mis en mémoire (toujours en direct).
   - data/acces.json (verrou d'accès) : jamais mis en mémoire. */
var VERSION = "v1", CACHE = "mf-" + VERSION;
var SHELL = ["./", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL).catch(function () { }); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) { return Promise.all(ks.filter(function (k) { return k.indexOf("mf-") === 0 && k !== CACHE; }).map(function (k) { return caches.delete(k); })); }).then(function () { return self.clients.claim(); }));
});

function keyOf(url) { var u = new URL(url); return u.origin + u.pathname; }   /* sans ?t=… : une seule copie par fichier */
function tag(r) { return r.headers.get("etag") || r.headers.get("last-modified") || r.headers.get("content-length") || ""; }
function tell(msg) { return self.clients.matchAll({ type: "window" }).then(function (cs) { cs.forEach(function (c) { c.postMessage(msg); }); }); }

function swr(req, kind) {
  var key = keyOf(req.url);
  return caches.open(CACHE).then(function (c) {
    return c.match(key).then(function (hit) {
      var net = fetch(kind === "page" ? new Request(req.url, { cache: "no-cache" }) : req).then(function (r) {
        if (r && r.ok) {
          var old = hit ? tag(hit) : null, fresh = tag(r);
          c.put(key, r.clone()).then(function () { if (hit && old !== fresh) tell({ t: kind, url: key }); });
        }
        return r;
      }).catch(function () { return null; });
      if (hit) { net.catch(function () { }); return hit; }
      return net.then(function (r) { return r || new Response("hors ligne", { status: 503 }); });
    });
  });
}

self.addEventListener("fetch", function (e) {
  var req = e.request; if (req.method !== "GET") return;
  var u = new URL(req.url);
  if (/acces\.json$/.test(u.pathname)) return;
  var own = u.origin === self.location.origin, raw = u.hostname === "raw.githubusercontent.com" && /\/data\/[^/]+\.json$/.test(u.pathname);
  if (req.mode === "navigate" && own) { e.respondWith(swr(new Request(self.registration.scope), "page")); return; }
  if (own && /\/data\/[^/]+\.json$/.test(u.pathname)) { e.respondWith(swr(req, "data")); return; }
  if (raw) { e.respondWith(swr(req, "data")); return; }
  if (own && /(manifest\.webmanifest|\/icons\/|\/ai\/[^/]+\.json)$/.test(u.pathname)) { e.respondWith(swr(req, "asset")); return; }
});
