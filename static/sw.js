/* ARC service worker — the minimum that makes the app installable.
   Deliberately does NOT cache the app shell: ARC is served from your own
   machine and changes often, so caching would hand back stale pages. It only
   adds a friendly fallback when the local server isn't running. */

self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  if (e.request.mode !== "navigate") return;   // let everything else hit the network normally
  e.respondWith(
    fetch(e.request).catch(() =>
      new Response(
        "<!doctype html><meta charset=utf-8><style>" +
        "body{margin:0;height:100vh;display:grid;place-items:center;background:#04070c;" +
        "color:#5fd9ff;font-family:ui-monospace,monospace;text-align:center;letter-spacing:.1em}" +
        "p{opacity:.7;font-size:13px}</style>" +
        "<div><h1 style='letter-spacing:.4em;font-weight:400'>ARC</h1>" +
        "<p>The core isn't running.<br>Launch it from the ARC icon, then reload.</p></div>",
        { headers: { "Content-Type": "text/html" } }
      )
    )
  );
});
