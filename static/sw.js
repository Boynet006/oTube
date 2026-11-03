// oTube service worker: makes the site installable as an app. Everything still comes
// from the server (videos are local anyway); when the server can't be reached, pages
// show a small "oTube is offline" screen instead of the browser's error page.
var OFFLINE = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1"><title>oTube is offline</title>' +
  '<style>html,body{height:100%;margin:0;background:#0f0f0f;color:#f1f1f1;font:15px system-ui,sans-serif}' +
  'main{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:16px;text-align:center}' +
  'b{font-size:20px}span{color:#aaa;max-width:340px}button{margin-top:8px;padding:10px 18px;border:0;border-radius:18px;background:#f1f1f1;color:#0f0f0f;font-weight:600;cursor:pointer}</style>' +
  '</head><body><main><b>Can’t reach oTube</b><span>Make sure the PC running oTube is on and you’re on the same network.</span>' +
  '<button onclick="location.reload()">Try again</button></main></body></html>';

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) {
  if (e.request.mode !== "navigate") return;   // only pages; videos and APIs go straight through
  e.respondWith(fetch(e.request).catch(function () {
    return new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } });
  }));
});
