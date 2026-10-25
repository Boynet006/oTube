// oTube settings page: autosave, modified dots + per-row reset, search,
// scroll-spy navigation, live previews, presets, library tools.
(function () {
  "use strict";
  var S = window.OTUBE, D = window.OTUBE_DEFAULTS, root = document.getElementById("settings");
  if (!root) return;

  // per-browser overrides that a setting replaces when changed here
  var LOCAL = {
    view: ["kidtube.view"], thumbw: ["kidtube.thumbw"], miniMode: ["otube.miniMode"], display: ["otube.display"],
    autoplay: ["otube.player:autoplay"], volume: ["otube.player:vol"], theater: ["otube.player:theater"],
    captionsOn: ["otube.player:ccOn"]
  };
  function dropLocal(key) {
    (LOCAL[key] || []).forEach(function (k) {
      try {
        var p = k.split(":");
        if (p.length === 1) { localStorage.removeItem(k); return; }
        var o = JSON.parse(localStorage.getItem(p[0]) || "{}"); delete o[p[1]];
        localStorage.setItem(p[0], JSON.stringify(o));
      } catch (e) {}
    });
  }
  function getv(key, obj) { var p = key.split("."); return p.length > 1 ? obj[p[0]][p[1]] : obj[key]; }
  function setv(key, v) { var p = key.split("."); if (p.length > 1) S[p[0]][p[1]] = v; else S[key] = v; }
  function rowOf(key) { return root.querySelector('.srow[data-key="' + key + '"]'); }

  // ---- modified dots ----
  function markModified(key) {
    var row = rowOf(key); if (!row) return;
    row.classList.toggle("modified", String(getv(key, S)) !== String(getv(key, D)));
  }
  root.querySelectorAll(".srow[data-key]").forEach(function (r) { markModified(r.getAttribute("data-key")); });

  // ---- save ----
  function flashSaved(key) {
    var row = rowOf(key); if (!row) return;
    row.classList.remove("saved"); void row.offsetWidth; row.classList.add("saved");
  }
  function save(key, v) {
    setv(key, v);
    var top = key.split(".")[0], body = {};
    body[top] = S[top];
    dropLocal(top);
    if (top === "display") applyDisplay();
    markModified(key);
    return fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); }).then(function (d) { if (d.ok) flashSaved(key); });
  }

  // ---- controls ----
  function num(v) { var n = Number(v); return v === "" || isNaN(n) ? v : n; }
  function syncControl(key) {
    var v = getv(key, S), el = root.querySelector('[data-k="' + key + '"]');
    if (!el) return;
    if (el.tagName === "SELECT") el.value = String(v);
    else if (el.classList.contains("tgl")) { el.classList.toggle("on", !!v); el.setAttribute("aria-checked", v ? "true" : "false"); }
    else if (el.type === "range") { el.value = Math.round(v * (+el.getAttribute("data-scale") || 1)); el.dispatchEvent(new Event("input")); }
    else el.querySelectorAll("[data-v]").forEach(function (b) {
      var on = b.getAttribute("data-v") === String(v); b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
    });
  }
  root.querySelectorAll("select[data-k]").forEach(function (el) {
    el.addEventListener("change", function () { save(el.getAttribute("data-k"), num(el.value)); });
  });
  root.querySelectorAll(".tgl[data-k]").forEach(function (el) {
    el.addEventListener("click", function () {
      var on = !el.classList.contains("on");
      el.classList.toggle("on", on); el.setAttribute("aria-checked", on ? "true" : "false");
      save(el.getAttribute("data-k"), on);
    });
  });
  root.querySelectorAll("[role=radiogroup][data-k]").forEach(function (g) {
    var pick = function (b) {
      g.querySelectorAll("[data-v]").forEach(function (x) { var on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-checked", on ? "true" : "false"); });
      save(g.getAttribute("data-k"), b.getAttribute("data-v"));
    };
    g.addEventListener("click", function (e) { var b = e.target.closest("[data-v]"); if (b) pick(b); });
    g.addEventListener("keydown", function (e) {   // arrow keys move within the radio group
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].indexOf(e.key) === -1) return;
      var bs = [].slice.call(g.querySelectorAll("[data-v]")), i = bs.indexOf(document.activeElement);
      if (i === -1) return;
      e.preventDefault();
      var n = bs[(i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + bs.length) % bs.length];
      n.focus(); pick(n);
    });
  });
  var tpCard = document.getElementById("tp-card");
  root.querySelectorAll("input[type=range][data-k]").forEach(function (el) {
    var k = el.getAttribute("data-k"), scale = +el.getAttribute("data-scale") || 1, unit = el.getAttribute("data-unit") || "";
    var out = el.parentElement.querySelector("output");
    var fill = function () { el.style.setProperty("--fill", ((el.value - el.min) / (el.max - el.min) * 100) + "%"); };
    fill();
    el.addEventListener("input", function () {
      fill(); if (out) out.textContent = el.value + unit;
      if (k.indexOf("display.") === 0) { setv(k, el.value / scale); applyDisplay(); }
      if (k === "thumbw" && tpCard) tpCard.style.width = Math.round(el.value * 0.42) + "px";
    });
    el.addEventListener("change", function () { save(k, el.value / scale); });
    if (k === "thumbw" && tpCard) tpCard.style.width = Math.round(el.value * 0.42) + "px";
  });

  // ---- per-row reset ----
  root.addEventListener("click", function (e) {
    var b = e.target.closest("[data-reset]"); if (!b) return;
    var key = b.getAttribute("data-reset");
    save(key, getv(key, D)); syncControl(key);
  });

  // ---- display: live + presets ----
  function applyDisplay() {
    var d = S.display, r = document.documentElement;
    r.style.setProperty("--app-b", d.b); r.style.setProperty("--app-c", d.c);
    r.classList.toggle("app-filter", d.b < 1 || d.c !== 1);
    r.style.setProperty("--app-warm", (d.n ? d.w / 100 * 0.45 : 0).toFixed(3));
    root.classList.toggle("night-off", !d.n);
  }
  applyDisplay();
  root.querySelectorAll("[data-preset]").forEach(function (b) {
    b.addEventListener("click", function () {
      var p = b.getAttribute("data-preset");
      var v = p === "normal" ? { b: 1, c: 1, n: false, w: S.display.w } : p === "dim" ? { b: 0.6, c: 1.05, n: false, w: S.display.w } : { b: 0.45, c: 1.05, n: true, w: 70 };
      S.display = v; ["display.b", "display.c", "display.n", "display.w"].forEach(syncControl);
      save("display.b", v.b).then(function () { ["display.c", "display.n", "display.w"].forEach(markModified); });
    });
  });

  // ---- search ----
  var q = document.getElementById("set-search"), empty = document.getElementById("set-empty");
  function filter() {
    var t = q.value.trim().toLowerCase(), any = false;
    root.querySelectorAll(".set-sec").forEach(function (sec) {
      var title = sec.querySelector("h2").textContent.toLowerCase(), secHit = t && title.indexOf(t) !== -1, shown = 0;
      sec.querySelectorAll("[data-search]").forEach(function (r) {
        var label = r.querySelector(".sr-text b");
        var hit = !t || secHit || r.getAttribute("data-search").indexOf(t) !== -1 ||
          (label && label.textContent.toLowerCase().indexOf(t) !== -1);
        r.hidden = !hit; if (hit) shown++;
      });
      sec.hidden = !!t && !shown && !secHit;
      if (!sec.hidden) any = true;
    });
    empty.hidden = any; empty.querySelector("span").textContent = q.value;
  }
  q.addEventListener("input", filter);
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (e.key === "/" && !(t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT"))) { e.preventDefault(); q.focus(); }
    if (e.key === "Escape" && t === q) { q.value = ""; filter(); q.blur(); }
  });

  // ---- scroll-spy navigation ----
  var links = [].slice.call(document.querySelectorAll(".set-nav__item"));
  var secs = links.map(function (a) { return document.getElementById(a.getAttribute("data-sec")); });
  function spy() {
    var y = window.scrollY + 140, cur = secs[0];
    secs.forEach(function (s) { if (s && !s.hidden && s.offsetTop <= y) cur = s; });
    if (window.innerHeight + window.scrollY >= document.body.scrollHeight - 4) cur = secs.filter(function (s) { return !s.hidden; }).pop();
    links.forEach(function (a) { var on = cur && a.getAttribute("data-sec") === cur.id; a.classList.toggle("on", on); if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
  }
  window.addEventListener("scroll", spy, { passive: true }); spy();
  links.forEach(function (a) {
    a.addEventListener("click", function (e) {
      var s = document.getElementById(a.getAttribute("data-sec")); if (!s) return;
      e.preventDefault();
      window.scrollTo({ top: s.offsetTop - 80, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      history.replaceState(null, "", "#" + s.id);
    });
  });
  if (location.hash) { var h = document.querySelector(location.hash); if (h) setTimeout(function () { window.scrollTo(0, h.offsetTop - 80); }, 50); }

  // ---- library tools ----
  var rescan = document.getElementById("rescan");
  if (rescan) rescan.addEventListener("click", function () {
    rescan.disabled = true; rescan.textContent = "Scanning…";
    fetch("/api/rescan", { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
      Object.keys(d.stats || {}).forEach(function (k) {
        document.querySelectorAll('[data-stat="' + k + '"]').forEach(function (el) { el.textContent = d.stats[k]; });
      });
      rescan.textContent = "✓ Up to date";
      setTimeout(function () { rescan.disabled = false; rescan.textContent = "↻ Rescan now"; }, 2000);
    }).catch(function () { rescan.disabled = false; rescan.textContent = "↻ Rescan now"; });
  });

  // ---- reset all ----
  document.getElementById("set-reset").addEventListener("click", function () {
    window.otubeConfirm({ title: "Reset all settings?", text: "Every setting goes back to its default.", confirm: "Reset all", danger: true })
      .then(function (ok) {
        if (!ok) return;
        Object.keys(LOCAL).forEach(dropLocal);
        fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reset: true }) })
          .then(function () { location.reload(); });
      });
  });
})();
