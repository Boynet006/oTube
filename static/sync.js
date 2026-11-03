// Watch progress shared by every device. Pages keep using localStorage
// ("kidtube.progress"); this file merges the server's copy in before they run
// (window.OTUBE_PROGRESS, embedded in the page) and sends local changes back.
// Each entry carries ts (when it changed): the newest one wins; deletions
// ("mark as unwatched") travel as {del: 1, ts}.
(function () {
  "use strict";
  var KEY = "kidtube.progress";
  var ls; try { ls = window.localStorage; ls.getItem(KEY); } catch (e) { return; }
  var setItem = Storage.prototype.setItem;
  var known = {};   // what the server has, as far as we know

  function read() { try { return JSON.parse(ls.getItem(KEY) || "{}") || {}; } catch (e) { return {}; } }
  function same(a, b) {
    return a.t === b.t && a.d === b.d && (a.ts || 1) === (b.ts || 1) && !!a.w === !!b.w;
  }
  function merge(server) {
    var local = read(), changed = false;
    Object.keys(server || {}).forEach(function (id) {
      var s = server[id], l = local[id];
      known[id] = s;
      if (l ? (s.ts || 1) > (l.ts || 1) : !s.del) {
        if (s.del) delete local[id];
        else { local[id] = { t: s.t, d: s.d, ts: s.ts }; if (s.w) local[id].w = true; }
        changed = true;
      }
    });
    if (changed) setItem.call(ls, KEY, JSON.stringify(local));
    return changed;
  }
  function changes() {
    var local = read(), out = {}, n = 0;
    Object.keys(local).forEach(function (id) {
      var l = local[id], k = known[id];
      if (l && (!k || k.del || !same(k, l))) { out[id] = { t: l.t, d: l.d, ts: l.ts || 1, w: l.w ? 1 : 0 }; n++; }
    });
    Object.keys(known).forEach(function (id) {
      if (!known[id].del && !local[id]) { out[id] = { del: 1, ts: Date.now() }; n++; }
    });
    return n ? out : null;
  }
  function push(leaving) {
    var c = changes();
    if (!c) return;
    var before = {};
    Object.keys(c).forEach(function (id) { before[id] = known[id]; known[id] = c[id]; });
    var body = JSON.stringify({ entries: c });
    if (leaving && navigator.sendBeacon) { navigator.sendBeacon("/api/progress", new Blob([body], { type: "application/json" })); return; }
    fetch("/api/progress", { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true })
      .then(function (r) { if (!r.ok) throw new Error(r.status); })
      .catch(function () {   // try again with the next change
        Object.keys(before).forEach(function (id) { if (before[id]) known[id] = before[id]; else delete known[id]; });
      });
  }

  var timer = null;
  Storage.prototype.setItem = function (k, v) {
    setItem.call(this, k, v);
    if (this === ls && k === KEY) { clearTimeout(timer); timer = setTimeout(push, 1500); }
  };
  window.addEventListener("pagehide", function () { push(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { push(true); return; }
    // back on this tab: pick up what other devices watched meanwhile
    fetch("/api/progress").then(function (r) { return r.json(); }).then(function (d) {
      if (merge(d.progress) && window.otubeProgressChanged) window.otubeProgressChanged();
    }).catch(function () {});
  });
  merge(window.OTUBE_PROGRESS);
  setTimeout(push, 3000);   // send what only this device has (first run on a device)
})();
