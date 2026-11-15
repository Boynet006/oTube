// Similar videos: the same footage saved more than once (see otube/similar.py).
(function () {
  var root = document.getElementById("sv");
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var SIZES = JSON.parse(root.getAttribute("data-sizes") || "[0]");
  var data = null;                 // last /api/similar answer
  var marked = {};                 // id -> true: marked for removal
  var removing = {};               // id -> "Deleting…"
  var failed = {};                 // id -> why it was kept
  var folders = null;              // [{path, name, depth, count}] for the picker
  var pollT = null, lastFull = 0;

  function esc(t) { return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function size(b) {
    var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return (i ? b.toFixed(b < 10 ? 1 : 0) : b) + " " + u[i];
  }
  function num(n) { return Number(n).toLocaleString(); }
  function plural(n, w) { return num(n) + " " + w + (n === 1 ? "" : "s"); }
  function toast(msg) {
    var t = $("note-toast");
    if (!t) { t = document.createElement("div"); t.id = "note-toast"; t.className = "note-toast"; document.body.appendChild(t); }
    t.classList.toggle("raised", !$("sv-bar").hidden);
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.classList.remove("show"); }, 4000);
  }
  function api(url, body) {
    return fetch(url, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // ---- What to search ----
  function nameOf(path) {
    var f = (folders || []).find(function (x) { return x.path === path; });
    return f ? f.name : path.replace(/\/+$/, "").split("/").pop() || path;
  }
  function sizeLabel(b) { return b ? size(b) : "Any"; }
  function renderScope() {
    var sc = data.scope;
    [["include", "sv-include", "Whole library"], ["exclude", "sv-exclude", "Nothing"]].forEach(function (x) {
      var box = $(x[1]), list = sc[x[0]];
      box.innerHTML = (list.length ? list.map(function (p) {
        return '<span class="sv-chip" title="' + esc(p) + '">' + esc(nameOf(p)) +
          '<button type="button" class="sv-chip__x" data-kind="' + x[0] + '" data-path="' + esc(p) + '" aria-label="Remove ' + esc(nameOf(p)) + '">✕</button></span>';
      }).join("") : '<span class="sv-chip sv-chip--none">' + x[2] + '</span>') +
        '<button type="button" class="sv-chip sv-chip--add" data-add="' + x[0] + '">+ Add folder</button>';
    });
    $("sv-min").innerHTML = SIZES.map(function (b) {
      var on = b === sc.min_size;
      return '<button type="button" role="radio" aria-checked="' + on + '" class="' + (on ? "on" : "") + '" data-min="' + b + '">' + sizeLabel(b) + '</button>';
    }).join("");
    var parts = [sc.include.length ? sc.include.map(nameOf).join(", ") : "Whole library"];
    if (sc.exclude.length) parts.push("except " + sc.exclude.map(nameOf).join(", "));
    if (sc.min_size) parts.push(sizeLabel(sc.min_size) + " and up");
    $("sv-scope-line").textContent = parts.join(" · ");
  }
  function setOpen(open) {
    $("sv-scope").classList.toggle("folded", !open);
    $("sv-scope-sum").setAttribute("aria-expanded", open ? "true" : "false");
    store("otube.similar.scopeOpen", open ? "1" : "0");
  }
  $("sv-scope-sum").addEventListener("click", function () { setOpen($("sv-scope").classList.contains("folded")); });
  $("sv-scope-done").addEventListener("click", function () { setOpen(false); });
  function saveScope(patch) {
    api("/api/similar/settings", patch).then(apply).catch(function () { toast("Couldn't save the choice"); });
  }
  $("sv-scope-body").addEventListener("click", function (e) {
    var x = e.target.closest(".sv-chip__x");
    if (x) {
      var kind = x.getAttribute("data-kind"), patch = {};
      patch[kind] = data.scope[kind].filter(function (p) { return p !== x.getAttribute("data-path"); });
      saveScope(patch); return;
    }
    var add = e.target.closest("[data-add]");
    if (add) { openPicker(add.getAttribute("data-add"), add); return; }
    var m = e.target.closest("[data-min]");
    if (m) saveScope({ min_size: +m.getAttribute("data-min") });
  });

  // folder picker: the library's folders and subfolders
  var pick = $("sv-pick"), pickKind = null, pickFrom = null;
  function drawPicker() {
    var q = $("sv-pick-filter").value.trim().toLowerCase(), taken = data.scope[pickKind];
    var rows = (folders || []).filter(function (f) { return !q || f.path.toLowerCase().indexOf(q) >= 0; });
    $("sv-pick-list").innerHTML = rows.length ? rows.map(function (f) {
      var has = taken.indexOf(f.path) >= 0;
      return '<button type="button" class="sv-pick__row" style="--d:' + (q ? 0 : f.depth) + '" data-path="' + esc(f.path) + '"' +
        (has ? ' disabled title="Already added"' : ' title="' + esc(f.path) + '"') + '><span>' + esc(q ? f.path : f.name) +
        '</span><span class="sv-pick__n">' + num(f.count) + '</span></button>';
    }).join("") : '<div class="browse-empty">No folders match.</div>';
  }
  function openPicker(kind, from) {
    pickKind = kind; pickFrom = from;
    $("sv-pick-h").textContent = kind === "include" ? "Look in" : "Leave out";
    $("sv-pick-filter").value = "";
    pick.hidden = false;
    (folders ? Promise.resolve() : api("/api/similar/folders").then(function (d) { folders = d.folders; }))
      .then(drawPicker).then(function () { $("sv-pick-filter").focus(); });
  }
  function closePicker() { pick.hidden = true; if (pickFrom) pickFrom.focus(); }
  $("sv-pick-filter").addEventListener("input", drawPicker);
  pick.addEventListener("click", function (e) {
    if (e.target === pick || e.target.closest("[data-close]")) { closePicker(); return; }
    var row = e.target.closest(".sv-pick__row");
    if (!row || row.disabled) return;
    var patch = {}; patch[pickKind] = data.scope[pickKind].concat(row.getAttribute("data-path"));
    pick.hidden = true; saveScope(patch);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pick.hidden) { e.stopPropagation(); closePicker(); } }, true);

  // ---- Strict / Normal / Loose: re-groups from the stored fingerprints, nothing is re-read ----
  $("sv-level").addEventListener("click", function (e) {
    var b = e.target.closest("[data-level]");
    if (b && data && b.getAttribute("data-level") !== data.level) saveScope({ level: b.getAttribute("data-level") });
  });

  // ---- Analyse ----
  $("sv-analyse").addEventListener("click", function () {
    api("/api/similar/analyse", {}).then(function (d) { data.task = d.task; renderStatus(); poll(); });
  });
  $("sv-cancel").addEventListener("click", function () {
    api("/api/similar/cancel", {}).then(function () { toast("Stopping… videos already analysed are kept"); });
  });
  function renderStatus() {
    var t = data.task, s = $("sv-status");
    $("sv-analyse").hidden = t.running; $("sv-cancel").hidden = !t.running;
    if (t.running) { s.innerHTML = '<span class="sv-spin" aria-hidden="true"></span>Analysing ' + num(t.done) + " of " + plural(t.total, "video") + "…"; return; }
    var bits = [];
    bits.push(data.pending ? "Still to analyse: " + num(data.pending) : "All analysed");
    if (data.unreadable) bits.push(num(data.unreadable) + " couldn’t be read");
    s.textContent = bits.join(" · ");
    $("sv-analyse").classList.toggle("primary", data.pending > 0);
  }
  function poll() {
    clearTimeout(pollT);
    api("/api/similar/status").then(function (t) {
      var was = data.task.running;
      data.task = t; renderStatus();
      if (t.running) {
        if (Date.now() - lastFull > 8000) load();   // groups fill in as it goes
        pollT = setTimeout(poll, 1500);
      } else if (was) load();
    }).catch(function () { pollT = setTimeout(poll, 5000); });
  }

  // ---- Groups ----
  function members(g) { return g.members.filter(function (m) { return !removing[m.id]; }); }
  function render() {
    var groups = data.groups, box = $("sv-groups");
    [].forEach.call($("sv-level").querySelectorAll("button"), function (b) {
      var on = b.getAttribute("data-level") === data.level;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
    });
    var ids = {};
    groups.forEach(function (g) { g.members.forEach(function (m) { ids[m.id] = 1; }); });
    Object.keys(marked).forEach(function (id) { if (!ids[id]) delete marked[id]; });
    $("sv-sum-text").innerHTML = groups.length
      ? "<b>" + num(groups.length) + "</b> group" + (groups.length === 1 ? "" : "s") + " among " + plural(data.videos, "video")
      : data.videos ? (data.pending === data.videos ? "Nothing analysed yet among " + plural(data.videos, "video") + ". Click “Analyse new videos”."
                                                    : "No similar videos among " + plural(data.videos, "video") + ".")
                    : "No videos in this choice.";
    $("sv-mark-all").hidden = !groups.length;
    box.innerHTML = groups.map(function (g, gi) {
      return '<section class="sv-group" data-g="' + gi + '" aria-label="Group ' + (gi + 1) + '">' +
        '<div class="sv-group__head">' + g.members.length + ' copies · ' + size(g.size) + '</div><div class="sv-row-cards">' +
        g.members.map(function (m) { return cardHtml(m, gi); }).join("") + '</div></section>';
    }).join("");
    renderBar();
  }
  function cardHtml(m, gi) {
    var st = removing[m.id] || failed[m.id], cls = "sv-card" + (m.best ? " best" : "") + (marked[m.id] ? " marked" : "") +
      (removing[m.id] ? " busy" : "") + (failed[m.id] ? " kept" : "");
    return '<div class="' + cls + '" tabindex="0" data-g="' + gi +
      '" data-id="' + m.id + '" data-name="' + esc(m.title) + '" data-path="' + esc(m.path) + '" title="' + esc(m.path) +
      '\nClick: play in the mini player · Circle or Space: mark for removal">' +
      '<button type="button" class="sv-check" aria-pressed="' + (marked[m.id] ? "true" : "false") + '" title="Mark for removal" aria-label="Mark ' + esc(m.title) + ' for removal">✓</button>' +
      '<div class="sv-thumb"><img loading="lazy" alt="" src="/thumb/' + m.id + '/frame?pct=30"><span class="badge">' + m.dur_h + '</span>' +
      (st ? '<span class="sv-state">' + esc(st) + '</span>' : '') + '</div>' +
      '<div class="sv-card__body"><div class="sv-card__name">' + (m.best ? '<span class="sv-star" title="Suggested best copy">★</span> ' : '') + esc(m.title) + '</div>' +
      '<div class="sv-card__meta">' + (m.w ? m.w + "×" + m.h : "?") + " · " + m.size_h + " · " + esc(m.folder) + '</div></div>' +
      '<button type="button" class="card__more" title="More actions" aria-label="More actions for ' + esc(m.title) + '" aria-haspopup="menu">…</button></div>';
  }
  function findCard(el) { return el && el.closest && el.closest(".sv-card"); }
  function toggle(card) {
    var id = card.getAttribute("data-id"), g = data.groups[+card.getAttribute("data-g")];
    if (removing[id]) return;
    if (!marked[id]) {
      var left = members(g).filter(function (m) { return !marked[m.id] && m.id !== id; });
      if (!left.length) { toast("At least one copy in each group is kept"); return; }
      marked[id] = true;
    } else delete marked[id];
    card.classList.toggle("marked", !!marked[id]);
    card.querySelector(".sv-check").setAttribute("aria-pressed", marked[id] ? "true" : "false");
    renderBar();
  }
  var groupsBox = $("sv-groups");
  // Click plays (mini player, so the list stays put; GOM when GOM mode is on); the circle marks.
  function play(c) {
    var path = "/watch/" + c.getAttribute("data-id");
    if (window.otubeGom && window.otubeGom.play && window.otubeGom.play(path)) return;
    if (window.otubeMini && window.otubeMini.play) window.otubeMini.play(path); else location.href = path;
  }
  groupsBox.addEventListener("click", function (e) {
    if (e.target.closest(".card__more")) return;   // the … button never marks or plays
    var c = findCard(e.target); if (!c || removing[c.getAttribute("data-id")]) return;
    if (e.target.closest(".sv-check")) toggle(c); else play(c);
  });
  groupsBox.addEventListener("keydown", function (e) {
    var c = findCard(e.target);
    if (!c || e.target !== c) return;
    if (e.key === " ") { e.preventDefault(); toggle(c); }
    else if (e.key === "Enter") { e.preventDefault(); play(c); }
    else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      var all = [].slice.call(groupsBox.querySelectorAll(".sv-card")), i = all.indexOf(c) + (e.key === "ArrowRight" ? 1 : -1);
      if (all[i]) { e.preventDefault(); all[i].focus(); }
    }
  });
  $("sv-mark-all").addEventListener("click", function () {
    data.groups.forEach(function (g) {
      g.members.forEach(function (m) { if (!m.best && !removing[m.id]) marked[m.id] = true; else delete marked[m.id]; });
    });
    render();
  });

  // ---- Removal bar ----
  function markedList() {
    var out = [];
    data.groups.forEach(function (g) { g.members.forEach(function (m) { if (marked[m.id]) out.push(m); }); });
    return out;
  }
  function renderBar() {
    var list = markedList(), n = list.length, total = list.reduce(function (a, m) { return a + m.size; }, 0);
    $("sv-bar").hidden = !n;
    $("sv-bar-count").textContent = plural(n, "video") + " marked · " + size(total);
    $("sv-bar-recycle").textContent = "Move " + num(n) + " to Recycle Bin (" + size(total) + ")";
  }
  $("sv-bar-clear").addEventListener("click", function () { marked = {}; render(); });
  $("sv-bar-recycle").addEventListener("click", function () { remove(true); });
  $("sv-bar-perm").addEventListener("click", function () { remove(false); });

  function remove(recycle) {
    var list = markedList(), n = list.length;
    if (!n) return;
    var total = size(list.reduce(function (a, m) { return a + m.size; }, 0));
    window.otubeConfirm({
      title: (recycle ? "Move " : "Delete ") + plural(n, "video") + (recycle ? " to the Recycle Bin?" : " permanently?"),
      text: (recycle ? "They can be restored from the Recycle Bin on this PC. " : "The files are deleted from disk. This can't be undone. ") +
        "Frees " + total + ". The best copy of each group is kept.",
      confirm: recycle ? "Move to Recycle Bin" : "Delete forever", danger: true,
    }).then(function (ok) {
      if (!ok) return;
      var ids = list.map(function (m) { return m.id; });
      ids.forEach(function (id) { removing[id] = recycle ? "Moving to Recycle Bin…" : "Deleting…"; delete marked[id]; delete failed[id]; });
      render();
      (recycle ? recycleAll(ids) : deleteAll(ids)).then(function (res) { finish(ids, res.gone, res.why); });
    });
  }
  function recycleAll(ids) {   // the on-PC helper moves the files (as on Home)
    var fd = new FormData(), helperDown = false;
    ids.forEach(function (v) { fd.append("vid", v); });
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
  function deleteAll(ids) {
    var gone = [];
    return ids.reduce(function (p, id) {
      return p.then(function () {
        return fetch("/delete/" + id, { method: "POST" }).then(function (r) { return r.json(); })
          .then(function (d) { if (d.ok) gone.push(id); }).catch(function () {});
      });
    }, Promise.resolve()).then(function () { return { gone: gone, why: "Kept: couldn’t be deleted" }; });
  }
  function dropIds(gone) {
    var set = {}; gone.forEach(function (id) { set[id] = 1; });
    data.groups = data.groups.map(function (g) {
      var ms = g.members.filter(function (m) { return !set[m.id]; });
      return { size: ms.reduce(function (a, m) { return a + m.size; }, 0), members: ms };
    }).filter(function (g) { return g.members.length > 1; });
  }
  function finish(ids, gone, why) {
    ids.forEach(function (id) { delete removing[id]; if (gone.indexOf(id) < 0) failed[id] = why; });
    dropIds(gone);
    data.videos -= gone.length;
    render();
    var kept = ids.length - gone.length;
    toast(num(gone.length) + " removed" + (kept ? " · " + num(kept) + " kept" : ""));
  }
  // deleted with the file menu
  document.addEventListener("otube:deleted", function (e) { dropIds([e.detail.id]); data.videos--; render(); });

  // ---- Load ----
  function apply(d) {
    data = d; lastFull = Date.now();
    renderScope(); render(); renderStatus();
    if (d.task.running) { clearTimeout(pollT); pollT = setTimeout(poll, 1500); }
  }
  function load() {
    return api("/api/similar").then(apply).catch(function () { $("sv-sum-text").textContent = "Couldn’t load similar videos."; });
  }
  api("/api/similar/folders").then(function (d) { folders = d.folders; if (data) renderScope(); }).catch(function () {});
  load().then(function () {
    if (!data) return;
    var sc = data.scope, saved = store("otube.similar.scopeOpen");
    // folds to one line once a choice exists
    setOpen(saved ? saved === "1" : !(sc.include.length || sc.exclude.length || sc.min_size));
  });
})();
