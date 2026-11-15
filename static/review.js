// Review videos: one row per video with frames from across it (see otube/review.py).
(function () {
  var root = document.getElementById("rv");
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var KEY = "otube.review", BATCH = 30;
  var all = [], view = [], shown = 0;
  var selected = {}, lastIdx = -1, removing = {}, kept = {};
  var params = new URLSearchParams(location.search);
  var picked = params.get("picked") === "1" ? readPicked() : null;
  var SORTS = {   // Home's orderings, exactly (otube/pages.py): name = lower-case title; the rest largest first
    name: function (a, b) { var x = a.title.toLowerCase(), y = b.title.toLowerCase(); return x < y ? -1 : x > y ? 1 : 0; },
    newest: function (a, b) { return b.mtime - a.mtime; },
    size: function (a, b) { return b.size - a.size; },
    duration: function (a, b) { return Math.floor(b.dur) - Math.floor(a.dur); },
  };
  var opts = load();

  // ---- small helpers ----
  function esc(t) { return String(t == null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function size(b) {
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return (i ? b.toFixed(b < 10 ? 1 : 0) : b) + " " + u[i];
  }
  function num(n) { return Number(n).toLocaleString(); }
  function plural(n, w) { return num(n) + " " + w + (n === 1 ? "" : "s"); }
  function clock(s) {
    s = Math.max(0, Math.floor(s || 0)); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (x < 10 ? "0" : "") + x;
  }
  function toast(msg) {
    var t = $("note-toast");
    if (!t) { t = document.createElement("div"); t.id = "note-toast"; t.className = "note-toast"; document.body.appendChild(t); }
    t.classList.toggle("raised", !$("rv-bar").hidden);
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.classList.remove("show"); }, 3500);
  }
  function load() {
    var d = { n: 6, size: "m", show: "all", path: "" };
    try { var s = JSON.parse(localStorage.getItem(KEY) || "{}"); Object.keys(d).forEach(function (k) { if (s[k] != null) d[k] = s[k]; }); } catch (e) {}
    if (params.has("path")) d.path = params.get("path");
    // the same sort as Home: from the link, else the default in Settings
    d.sort = params.get("sort") || (window.OTUBE || {}).sort || "name";
    if (!SORTS[d.sort] && d.sort !== "random") d.sort = "name";
    d.seed = +params.get("seed") || Math.floor(Math.random() * 1e6);
    return d;
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify({ n: opts.n, size: opts.size, show: opts.show, path: opts.path })); } catch (e) {}
  }
  function syncUrl() {   // path + sort (+ seed) live in the address, as on Home
    var u = new URLSearchParams();
    if (picked) u.set("picked", "1"); else if (opts.path) u.set("path", opts.path);
    if (opts.sort !== "name") u.set("sort", opts.sort);
    if (opts.sort === "random") u.set("seed", opts.seed);
    history.replaceState(null, "", "/review" + (u.toString() ? "?" + u : ""));
  }
  function readPicked() { try { return JSON.parse(sessionStorage.getItem("otube.reviewIds") || "null"); } catch (e) { return null; } }
  function isDone(id) {
    try { var e = JSON.parse(localStorage.getItem("kidtube.progress") || "{}")[id]; return !!(e && (e.w || (e.d && e.t / e.d >= 0.97))); }
    catch (err) { return false; }
  }

  // ---- options ----
  function paintOpts() {
    [].forEach.call($("rv-show").querySelectorAll("button"), function (b) {
      var on = b.getAttribute("data-show") === opts.show; b.classList.toggle("on", on); b.setAttribute("aria-checked", on);
    });
    [].forEach.call($("rv-size").querySelectorAll("button"), function (b) {
      var on = b.getAttribute("data-size") === opts.size; b.classList.toggle("on", on); b.setAttribute("aria-checked", on);
    });
    $("rv-n").value = opts.n; if (document.activeElement !== $("rv-n-num")) $("rv-n-num").value = opts.n;
    $("rv-sort").value = opts.sort;
    $("rv-reshuffle").hidden = opts.sort !== "random";
    $("rv-list").className = "rv-list rv--" + opts.size;
    $("rv-list").style.setProperty("--n", opts.n);   // one row of N frames per video
  }
  function setN(v) {
    v = Math.min(12, Math.max(3, Math.round(+v || 6)));
    if (v === opts.n) { paintOpts(); return; }
    opts.n = v; save(); paintOpts();
    [].forEach.call($("rv-list").querySelectorAll(".rv-row"), function (row) {   // re-cut the frames of the rows on screen
      var v = byId[row.getAttribute("data-id")]; if (v) row.querySelector(".rv-strip").innerHTML = frames(v);
    });
  }
  $("rv-n").addEventListener("input", function () { setN(this.value); });
  $("rv-n-num").addEventListener("change", function () { setN(this.value); });
  $("rv-n-num").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); setN(this.value); this.blur(); } });
  root.querySelector(".rv-step").addEventListener("click", function (e) {
    var b = e.target.closest("[data-step]"); if (b) setN(opts.n + +b.getAttribute("data-step"));
  });
  $("rv-size").addEventListener("click", function (e) {
    var b = e.target.closest("[data-size]"); if (!b) return; opts.size = b.getAttribute("data-size"); save(); paintOpts();
  });
  $("rv-show").addEventListener("click", function (e) {
    var b = e.target.closest("[data-show]"); if (!b) return; opts.show = b.getAttribute("data-show"); save(); paintOpts(); refresh();
  });
  $("rv-sort").addEventListener("change", function () {
    opts.sort = this.value; if (opts.sort === "random") opts.seed = Math.floor(Math.random() * 1e6);
    paintOpts(); syncUrl(); refresh();
  });
  $("rv-reshuffle").addEventListener("click", function () { opts.seed = Math.floor(Math.random() * 1e6); syncUrl(); refresh(); });
  $("rv-folder").addEventListener("change", function () {
    if (this.value === "__picked") return;
    picked = null; opts.path = this.value; save(); syncUrl();
    fetchList();
  });

  // ---- the list ----
  var byId = {};
  function fetchList(keepScroll) {
    var y = window.scrollY, had = shown;
    $("rv-sum").textContent = "Loading…";
    var req = picked ? fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vids: picked }) })
                     : fetch("/api/review?path=" + encodeURIComponent(opts.path || ""));
    return req.then(function (r) { if (!r.ok) throw 0; return r.json(); }).then(function (d) {
      all = d.videos; byId = {}; all.forEach(function (v) { byId[v.id] = v; });
      Object.keys(selected).forEach(function (id) { if (!byId[id]) delete selected[id]; });
      refresh(keepScroll ? had : 0);
      if (keepScroll) window.scrollTo(0, y);
    }).catch(function () { $("rv-sum").textContent = "Couldn’t load the videos."; });
  }
  function shuffled(list, seed) {   // stable for a seed, like Home's Random
    var s = seed >>> 0, rnd = function () {
      s = (s + 0x6D2B79F5) >>> 0; var t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    list.sort(function (a, b) { return a.id < b.id ? -1 : 1; });
    for (var i = list.length - 1; i > 0; i--) { var j = Math.floor(rnd() * (i + 1)), x = list[i]; list[i] = list[j]; list[j] = x; }
    return list;
  }
  function refresh(atLeast) {
    view = all.filter(function (v) {
      return opts.show === "all" || (opts.show === "watched") === isDone(v.id);
    });
    if (opts.sort === "random") shuffled(view, opts.seed); else view.sort(SORTS[opts.sort]);
    lastIdx = -1; shown = 0;
    $("rv-list").innerHTML = "";
    var total = view.reduce(function (a, v) { return a + v.size; }, 0);
    $("rv-sum").innerHTML = view.length ? "<b>" + plural(view.length, "video") + "</b> · " + size(total)
      : all.length ? "No " + opts.show + " videos here." : "No videos here.";
    $("rv-all").hidden = !view.length;
    more(Math.max(BATCH, atLeast || 0));
    renderBar();
  }
  function more(n) {
    var html = "", end = Math.min(view.length, shown + (n || BATCH));
    for (var i = shown; i < end; i++) html += rowHtml(view[i], i);
    shown = end;
    $("rv-list").insertAdjacentHTML("beforeend", html);
  }
  new IntersectionObserver(function (es) {   // the next rows as you scroll
    if (es[0].isIntersecting && shown < view.length) more();
  }, { rootMargin: "800px" }).observe($("rv-more"));

  function frames(v) {
    var out = "";
    for (var i = 0; i < opts.n; i++) {
      var pct = Math.round((i + 0.5) / opts.n * 1000) / 10;   // the middle of each slice: skips the very start and end
      out += '<button type="button" class="rv-frame" data-pct="' + pct + '" title="Play from here">' +
        '<img loading="lazy" alt="" src="/thumb/' + v.id + '/frame?fast=1&pct=' + pct + '">' +
        (v.dur ? '<span class="rv-time">' + clock(v.dur * pct / 100) + '</span>' : '') +
        '<span class="rv-use" role="button" tabindex="-1" title="Use as thumbnail" aria-label="Use this frame as the thumbnail">' +
        '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M21 19V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2zM8.5 13.5l2.5 3 3.5-4.5 4.5 6H5l3.5-4.5z"/></svg></span></button>';
    }
    return out;
  }
  function meta(v) {
    return [v.dur_h, v.size_h, v.w ? v.w + "×" + v.h : "", esc(v.folder)].filter(Boolean).join(" · ") +
      (isDone(v.id) ? ' · <span class="rv-done">Watched</span>' : "");
  }
  function rowHtml(v, i) {
    var st = removing[v.id] || kept[v.id];
    return '<div class="rv-row' + (selected[v.id] ? " selected" : "") + (removing[v.id] ? " busy" : "") + (kept[v.id] ? " kept" : "") +
      '" tabindex="0" data-i="' + i + '" data-rate="' + (v.rating || "") + '" data-id="' + v.id + '" data-name="' + esc(v.title) + '" data-path="' + esc(v.path) + '" data-dur="' + (v.dur || 0) + '">' +
      '<div class="rv-info">' +
      '<button type="button" class="sv-check rv-check" aria-pressed="' + !!selected[v.id] + '" title="Select" aria-label="Select ' + esc(v.title) + '">✓</button>' +
      '<div class="rv-text"><div class="rv-title" title="' + esc(v.path) + '">' + esc(v.title) + '</div>' +
      '<div class="rv-meta">' + meta(v) + '</div>' + (st ? '<div class="rv-state">' + esc(st) + '</div>' : '') + '</div>' + RATE.replace(/class="rate-btn" data-r="(\w+)"/g, function (m, r) {
        return 'class="rate-btn' + (v.rating === r ? " on" : "") + '" aria-pressed="' + (v.rating === r) + '" data-r="' + r + '"'; }) +
      '<button type="button" class="card__more" title="More actions" aria-label="More actions for ' + esc(v.title) + '" aria-haspopup="menu">…</button>' +
      '</div><div class="rv-strip">' + frames(v) + '</div></div>';
  }
  var RATE = '<div class="card__rate rv-rate" role="group" aria-label="Rate this video">' +
    '<button type="button" class="rate-btn" data-r="like" title="I like this" aria-label="Like"><span class="r-off"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M18.77 11h-4.23l1.52-4.94C16.38 5.03 15.54 4 14.38 4c-.58 0-1.14.24-1.52.65L7 11H3v10h4h1h9.43c1.06 0 1.98-.67 2.23-1.61l1.29-6C21.27 12.15 20.22 11 18.77 11zM7 20H4v-8h3v8zm12.98-6.83-1.29 6c-.08.35-.47.83-1.26.83H8v-8.61l5.6-6.06c.19-.21.48-.33.78-.33.26 0 .5.11.63.3.07.1.15.26.09.47l-1.52 4.94-.4 1.29h5.58c.41 0 .8.17 1.03.46.13.15.26.4.19.71z"/></svg></span><span class="r-on"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M3 11h3v10H3V11zm15.77 0h-4.23l1.52-4.94C16.38 5.03 15.54 4 14.38 4c-.58 0-1.14.24-1.52.65L7 11v10h10.43c1.06 0 1.98-.67 2.23-1.61l1.29-6C21.27 12.15 20.22 11 18.77 11z"/></svg></span></button>' +
    '<button type="button" class="rate-btn" data-r="dislike" title="I dislike this" aria-label="Dislike"><span class="r-off"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><g transform="rotate(180 12 12)"><path fill="currentColor" d="M18.77 11h-4.23l1.52-4.94C16.38 5.03 15.54 4 14.38 4c-.58 0-1.14.24-1.52.65L7 11H3v10h4h1h9.43c1.06 0 1.98-.67 2.23-1.61l1.29-6C21.27 12.15 20.22 11 18.77 11zM7 20H4v-8h3v8zm12.98-6.83-1.29 6c-.08.35-.47.83-1.26.83H8v-8.61l5.6-6.06c.19-.21.48-.33.78-.33.26 0 .5.11.63.3.07.1.15.26.09.47l-1.52 4.94-.4 1.29h5.58c.41 0 .8.17 1.03.46.13.15.26.4.19.71z"/></g></svg></span><span class="r-on"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><g transform="rotate(180 12 12)"><path fill="currentColor" d="M3 11h3v10H3V11zm15.77 0h-4.23l1.52-4.94C16.38 5.03 15.54 4 14.38 4c-.58 0-1.14.24-1.52.65L7 11v10h10.43c1.06 0 1.98-.67 2.23-1.61l1.29-6C21.27 12.15 20.22 11 18.77 11z"/></g></svg></span></button></div>';
  function rowEl(id) { return $("rv-list").querySelector('.rv-row[data-id="' + id + '"]'); }

  // ---- play, select, thumbnail ----
  function play(v, t) {
    var path = "/watch/" + v.id;
    if (window.otubeGom && window.otubeGom.play && window.otubeGom.play(path)) return;
    if (window.otubeMini && window.otubeMini.play) window.otubeMini.play(path, t || 0); else location.href = path + (t ? "?t=" + Math.floor(t) : "");
  }
  function setSel(id, on) { if (on) selected[id] = true; else delete selected[id]; var r = rowEl(id); if (r) paintSel(r); }
  function paintSel(r) {
    var on = !!selected[r.getAttribute("data-id")];
    r.classList.toggle("selected", on); r.querySelector(".rv-check").setAttribute("aria-pressed", on);
  }
  function toggle(row, shift) {
    var i = +row.getAttribute("data-i"), id = row.getAttribute("data-id"), on = !selected[id];
    if (removing[id]) return;
    if (shift && lastIdx !== -1) {
      for (var k = Math.min(lastIdx, i); k <= Math.max(lastIdx, i); k++) if (view[k] && !removing[view[k].id]) setSel(view[k].id, true);
    } else setSel(id, on);
    lastIdx = i; renderBar();
  }
  var list = $("rv-list");
  list.addEventListener("click", function (e) {
    var row = e.target.closest(".rv-row"); if (!row || e.target.closest(".card__more")) return;
    var v = byId[row.getAttribute("data-id")]; if (!v || removing[v.id]) return;
    if (e.target.closest(".rv-check")) { toggle(row, e.shiftKey); return; }
    var use = e.target.closest(".rv-use"), f = e.target.closest(".rv-frame");
    if (use && f) { e.stopPropagation(); useThumb(v, +f.getAttribute("data-pct")); return; }
    if (f) { play(v, v.dur * +f.getAttribute("data-pct") / 100); return; }
    if (e.target.closest(".rv-title")) play(v, 0);
  });
  list.addEventListener("click", function (e) {   // after script.js has rated it (capture phase)
    var b = e.target.closest(".rate-btn"), row = b && b.closest(".rv-row"); if (!row) return;
    b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop");   // replay the pop
    b.addEventListener("animationend", function () { b.classList.remove("pop"); }, { once: true });
    setTimeout(function () { var v = byId[row.getAttribute("data-id")]; if (v) v.rating = row.getAttribute("data-rate") || ""; }, 1500);
  });
  list.addEventListener("otube:play", function (e) { var v = byId[e.target.getAttribute("data-id")]; if (v) play(v, 0); });
  list.addEventListener("otube:renamed", function () { fetchList(true); toast("Renamed"); });
  list.addEventListener("keydown", function (e) {
    var row = e.target.closest(".rv-row"); if (!row || e.target !== row) return;
    if (e.key === " ") { e.preventDefault(); toggle(row, e.shiftKey); }
    else if (e.key === "Enter") { e.preventDefault(); play(byId[row.getAttribute("data-id")], 0); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      var next = e.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling;
      if (next) { e.preventDefault(); next.focus(); }
    }
  });
  function useThumb(v, pct) {
    fetch("/api/thumb/" + v.id, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pct: pct }) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (!d.ok) throw 0; toast("Thumbnail set (" + clock(d.t) + ")"); })
      .catch(function () { toast("Couldn’t set the thumbnail"); });
  }

  // ---- selection bar ----
  function ids() { return Object.keys(selected); }
  function clearSel() { selected = {}; lastIdx = -1; [].forEach.call(list.querySelectorAll(".rv-row.selected"), paintSel); renderBar(); }
  function renderBar() {
    var s = ids(), n = s.length, total = s.reduce(function (a, id) { return a + (byId[id] ? byId[id].size : 0); }, 0);
    $("rv-bar").hidden = !n;
    $("rv-count").textContent = plural(n, "video") + " selected · " + size(total);
    $("rv-all").textContent = n && n === view.length ? "Select none" : "Select all";
  }
  function selectAll() {
    var on = ids().length !== view.length;
    view.forEach(function (v) { if (on && !removing[v.id]) selected[v.id] = true; else delete selected[v.id]; });
    [].forEach.call(list.querySelectorAll(".rv-row"), paintSel); renderBar();
  }
  $("rv-all").addEventListener("click", selectAll);
  $("rv-clear").addEventListener("click", clearSel);
  ["rv-watched", "rv-unwatched"].forEach(function (b) {
    $(b).addEventListener("click", function () {
      var s = ids(), w = b === "rv-watched";
      if (window.otubeMarkWatched) window.otubeMarkWatched(s, w);
      toast(plural(s.length, "video") + " marked as " + (w ? "watched" : "unwatched")); clearSel();
      watchedChanged(s);
    });
  });
  $("rv-thumbs").addEventListener("click", function () { window.otubeBulkThumbs({ vids: ids() }, clearSel); });
  $("rv-save").addEventListener("click", function () {
    var s = ids(); window.otubeSave(s, "", function (msg) { toast(msg + " (" + plural(s.length, "video") + ")"); });
  });
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
    if (document.querySelector(".modal-overlay:not([hidden])")) return;
    if (e.key === "Escape" && ids().length) clearSel();
    else if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A") && view.length) { e.preventDefault(); if (ids().length !== view.length) selectAll(); }
  });

  // ---- delete: rows dim in place, then leave; the ones that stay say why ----
  $("rv-delete").addEventListener("click", function () {
    var s = ids(), n = s.length, total = size(s.reduce(function (a, id) { return a + byId[id].size; }, 0));
    window.otubeConfirm({
      title: "Move " + plural(n, "video") + " to the Recycle Bin?",
      text: "They can be restored from the Recycle Bin on this PC. Frees " + total + ".",
      confirm: "Move to Recycle Bin", danger: true,
    }).then(function (ok) {
      if (!ok) return;
      s.forEach(function (id) { removing[id] = "Moving to Recycle Bin…"; delete kept[id]; delete selected[id]; repaint(id); });
      renderBar();
      recycle(s).then(function (res) {
        var gone = {}; res.gone.forEach(function (id) { gone[id] = 1; });
        s.forEach(function (id) { delete removing[id]; if (!gone[id]) kept[id] = res.why; });
        all = all.filter(function (v) { return !gone[v.id]; });
        res.gone.forEach(function (id) { delete byId[id]; var r = rowEl(id); if (r) r.remove(); });
        view = view.filter(function (v) { return !gone[v.id]; });
        [].forEach.call(list.querySelectorAll(".rv-row"), function (r, i) { r.setAttribute("data-i", i); });
        shown = list.querySelectorAll(".rv-row").length;
        s.forEach(function (id) { if (!gone[id]) repaint(id); });
        var k = n - res.gone.length;
        toast(num(res.gone.length) + " moved to the Recycle Bin" + (k ? " · " + num(k) + " kept" : ""));
        var total = view.reduce(function (a, v) { return a + v.size; }, 0);
        $("rv-sum").innerHTML = view.length ? "<b>" + plural(view.length, "video") + "</b> · " + size(total) : "No videos here.";
      });
    });
  });
  function repaint(id) {
    var r = rowEl(id), v = byId[id]; if (!r || !v) return;
    var tmp = document.createElement("div"); tmp.innerHTML = rowHtml(v, +r.getAttribute("data-i"));
    r.replaceWith(tmp.firstChild);
  }
  function recycle(s) {   // the on-PC helper moves the files (as on Home)
    var fd = new FormData(), helperDown = false;
    s.forEach(function (v) { fd.append("vid", v); });
    return fetch("/delete_many", { method: "POST", body: fd }).then(function (r) { return r.json(); })
      .then(function (d) {
        return d.delegate.reduce(function (p, f) {
          return p.then(function () {
            if (helperDown) return;
            return fetch("http://127.0.0.1:8701/recycle", { method: "POST", body: f.path }).catch(function () { helperDown = true; });
          });
        }, Promise.resolve());
      })
      .then(function () { fd.append("done", "1"); return fetch("/delete_many", { method: "POST", body: fd }); })
      .then(function (r) { return r.json(); })
      .then(function (d) { return { gone: d.deleted || [], why: helperDown ? "Kept: the oTube helper isn’t running" : "Kept: couldn’t be moved" }; })
      .catch(function () { return { gone: [], why: "Kept: the request failed" }; });
  }
  document.addEventListener("otube:watched", function (e) { watchedChanged(e.detail.ids); });
  // A video whose watched state no longer fits the filter (Unwatched / Watched) slides out right away
  function watchedChanged(list) {
    var out = [];
    list.forEach(function (id) {
      var r = rowEl(id), v = byId[id]; if (!r || !v) return;
      r.querySelector(".rv-meta").innerHTML = meta(v);
      if (opts.show !== "all" && (opts.show === "watched") !== isDone(id)) out.push(id);
    });
    if (!out.length) return;
    var gone = {}; out.forEach(function (id) { gone[id] = 1; delete selected[id]; });
    view = view.filter(function (v) { return !gone[v.id]; });
    out.forEach(function (id) {
      var r = rowEl(id); if (!r) return;
      r.style.height = r.offsetHeight + "px"; r.offsetHeight;   // fix the height so it can collapse
      r.classList.add("leaving");
      setTimeout(function () { r.remove(); renumber(); }, 320);
    });
    shown -= out.length;
    summary(); renderBar();
    if (shown < BATCH && shown < view.length) more();   // keep the list filled
  }
  function renumber() { [].forEach.call(list.querySelectorAll(".rv-row"), function (r, i) { r.setAttribute("data-i", i); }); }
  function summary() {
    var total = view.reduce(function (a, v) { return a + v.size; }, 0);
    $("rv-sum").innerHTML = view.length ? "<b>" + plural(view.length, "video") + "</b> · " + size(total)
      : all.length ? "No " + opts.show + " videos left here." : "No videos here.";
    $("rv-all").hidden = !view.length;
  }
  // deleted with the file menu
  document.addEventListener("otube:deleted", function (e) {
    var id = e.detail.id, r = rowEl(id);
    all = all.filter(function (v) { return v.id !== id; }); view = view.filter(function (v) { return v.id !== id; });
    delete byId[id]; delete selected[id]; if (r) r.remove(); renderBar();
  });

  // ---- start ----
  paintOpts();
  fetch("/api/similar/folders").then(function (r) { return r.json(); }).then(function (d) {
    var sel = $("rv-folder");
    sel.innerHTML = (picked ? '<option value="__picked">Picked on Home (' + picked.length + ')</option>' : "") +
      '<option value="">Whole library</option>' + d.folders.map(function (f) {
        return '<option value="' + esc(f.path) + '">' + new Array(f.depth * 3 + 1).join(" ") + esc(f.name) + " (" + num(f.count) + ")</option>";
      }).join("");
    sel.value = picked ? "__picked" : opts.path;
    if (!picked && opts.path && sel.value !== opts.path) { sel.value = ""; opts.path = ""; save(); fetchList(); }   // that folder is gone
  }).catch(function () {});
  fetchList();
})();
