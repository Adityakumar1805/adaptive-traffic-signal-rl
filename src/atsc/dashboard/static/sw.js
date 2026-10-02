// Service worker for the live dashboard (served at /sw.js with the build id filled in).
//
// Purpose: when a free hosting instance is asleep, the first request can hang for up to a
// minute while it boots. A returning visitor then gets this page's cached shell after a few
// seconds instead of a blank tab, and the page shows "Waking up the server..." and connects
// as soon as the server answers.
//
// Strategy: network first, always. The cache is used only when the network fails or has not
// answered within NETWORK_TIMEOUT_MS, so a live server is never bypassed with stale files.
// Every deploy changes BUILD, which installs a new worker and deletes the old cache.
// API calls, the WebSocket and the health check are never touched.

const BUILD = "__ATSC_BUILD__";
const CACHE = "atsc-shell-" + BUILD;
const NETWORK_TIMEOUT_MS = 4000;
const SHELL = [
  "/",
  "/static/styles.css",
  "/static/icon.svg",
  "/static/js/main.js",
  "/static/js/model.js",
  "/static/js/transport.js",
  "/static/js/renderer.js",
  "/static/js/sprites.js",
  "/static/js/charts.js",
  "/static/js/ui.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL.map((url) => new Request(url, { cache: "no-cache" }))))
      .catch(() => undefined)            // a failed pre-cache must never block the page
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("atsc-shell-") && k !== CACHE)
                                      .map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// after one navigation had to fall back to the cache, serve the rest of that page load from
// the cache straight away instead of waiting NETWORK_TIMEOUT_MS for every file
let offlineUntil = 0;

function cacheable(url) {
  return url.origin === self.location.origin &&
         (url.pathname === "/" || url.pathname.startsWith("/static/"));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (!cacheable(url)) return;           // /api/*, /ws, /healthz, /sw.js: straight to the network
  const key = url.pathname === "/" ? "/" : url.pathname;
  event.respondWith(networkFirst(req, key));
});

// Only responses from this app are cached or preferred: the server marks them with an
// X-ATSC-Build header. A hosting proxy's error page or "please wait" page while the instance
// boots is passed through only when there is no cached copy of the page to show instead.
function ours(res) {
  return !!res && res.ok && res.type === "basic" && res.headers.has("x-atsc-build");
}

async function networkFirst(req, key) {
  const cache = await caches.open(CACHE);
  const network = fetch(req).then((res) => {
    if (ours(res)) {
      cache.put(key, res.clone()).catch(() => undefined);
      offlineUntil = 0;
    }
    return res;
  });
  const cached = await cache.match(key);
  if (!cached) return network;           // nothing to fall back to: just wait for the server
  if (Date.now() < offlineUntil) {
    network.catch(() => undefined);
    return cached;
  }
  let timer;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), NETWORK_TIMEOUT_MS); });
  try {
    const res = await Promise.race([network, timeout]);
    if (ours(res)) return res;
    if (res && res.status === 404) return res;     // a file that really is gone
  } catch (e) {
    /* network error: fall through to the cache */
  } finally {
    clearTimeout(timer);
  }
  network.catch(() => undefined);
  offlineUntil = Date.now() + 15000;
  return cached;
}
