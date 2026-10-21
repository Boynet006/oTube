// oTube client: search, sort, sidebar toggle, resume + autoplay + watched bars.
(function () {
  "use strict";
  var KEY = "kidtube.progress";  // legacy key name kept so saved progress survives the rename

  function loadProgress() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); }
    catch (e) { return {}; }
  }
  function saveProgress(p) {
    try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (e) {}
  }

  // --- Sidebar toggle ---
  var menuBtn = document.getElementById("menu-toggle");
  var sidebar = document.getElementById("sidebar");
  if (menuBtn && sidebar) {
    menuBtn.addEventListener("click", function () { sidebar.classList.toggle("hidden"); });
  }

  // --- Live search (home) ---
  var search = document.getElementById("search");
  var grid = document.getElementById("grid");
  var noResults = document.getElementById("no-results");
  if (search && grid) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      var shown = 0;
      grid.querySelectorAll(".card").forEach(function (c) {
        var match = !q || (c.getAttribute("data-title") || "").indexOf(q) !== -1;
        c.style.display = match ? "" : "none";
        if (match) shown++;
      });
      if (noResults) noResults.hidden = shown !== 0;
    });
    // Arriving from the watch page's search box (/?q=...): prefill + filter.
    var initialQ = new URLSearchParams(window.location.search).get("q");
    if (initialQ) {
      search.value = initialQ;
      search.dispatchEvent(new Event("input"));
    }
  }

  // --- Sort (home) ---
  var sortSel = document.getElementById("sort-select");
  if (sortSel) {
    sortSel.addEventListener("change", function () {
      var folder = sortSel.getAttribute("data-folder") || "";
      var url = "/?sort=" + encodeURIComponent(sortSel.value);
      if (folder) url += "&folder=" + encodeURIComponent(folder);
      window.location.href = url;
    });
  }

  // --- Shuffle play (home) ---
  var shuffleBtn = document.getElementById("shuffle-btn");
  if (shuffleBtn && grid) {
    shuffleBtn.addEventListener("click", function () {
      var cards = [].slice.call(grid.querySelectorAll(".card"))
        .filter(function (c) { return c.style.display !== "none"; });
      if (!cards.length) return;
      var pick = cards[Math.floor(Math.random() * cards.length)];
      var id = pick.getAttribute("data-id");
      if (id) window.location.href = "/watch/" + id;
    });
  }

  // --- Thumbnail size control (home) ---
  var sizeSlider = document.getElementById("size-slider");
  if (sizeSlider && grid) {
    var SIZE_KEY = "kidtube.thumbw";
    function applySize(w) {
      w = Math.min(+sizeSlider.max, Math.max(+sizeSlider.min, parseInt(w, 10) || 300));
      sizeSlider.value = w;
      grid.style.setProperty("--thumb-w", w + "px");
      var pct = (w - sizeSlider.min) / (sizeSlider.max - sizeSlider.min) * 100;
      sizeSlider.style.setProperty("--fill", pct + "%");
      grid.classList.toggle("compact", w < 210);
      try { localStorage.setItem(SIZE_KEY, w); } catch (e) {}
    }
    var saved;
    try { saved = localStorage.getItem(SIZE_KEY); } catch (e) {}
    applySize(saved || 300);
    sizeSlider.addEventListener("input", function () { applySize(sizeSlider.value); });
  }

  var viewCtl = document.getElementById("view-ctl");
  if (viewCtl && grid) {
    var VIEW_KEY = "kidtube.view";
    function applyView(v) {
      if (v !== "list") v = "grid";
      grid.classList.toggle("list", v === "list");
      viewCtl.querySelectorAll("button").forEach(function (b) {
        b.classList.toggle("active", b.getAttribute("data-view") === v);
      });
      try { localStorage.setItem(VIEW_KEY, v); } catch (e) {}
    }
    var savedView;
    try { savedView = localStorage.getItem(VIEW_KEY); } catch (e) {}
    applyView(savedView);
    viewCtl.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-view]");
      if (b) applyView(b.getAttribute("data-view"));
    });
  }

  // --- Folder browser modal (home) ---
  var browseModal = document.getElementById("browse-modal");
  var browseOpen = document.getElementById("browse-open");
  if (browseModal && browseOpen) {
    var crumbEl = document.getElementById("browse-crumb");
    var listEl = document.getElementById("browse-list");
    var currentEl = document.getElementById("browse-current");
    var addBtn = document.getElementById("browse-add");
    var closeBtn = document.getElementById("browse-close");
    var selPath = null;

    var FOLDER_SVG = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z"/></svg>';
    var UP_SVG = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20z"/></svg>';

    function setSelected(path, count) {
      selPath = path;
      if (path) {
        var n = count || 0;
        currentEl.textContent = path + " · " + n + " video" + (n === 1 ? "" : "s") + " here";
        addBtn.disabled = false;
      } else {
        currentEl.textContent = "Pick a folder";
        addBtn.disabled = true;
      }
    }

    function render(data) {
      // crumb
      if (data.path) {
        crumbEl.innerHTML = '<span>' + data.path + '</span>';
        setSelected(data.path, data.video_count);
      } else {
        crumbEl.textContent = "This PC — pick a drive";
        setSelected(null);
      }
      // list
      listEl.innerHTML = "";
      if (data.parent !== null && data.parent !== undefined) {
        var up = document.createElement("button");
        up.className = "browse-row up";
        up.innerHTML = UP_SVG + '<span class="name">..</span>';
        up.addEventListener("click", function () { navigate(data.parent); });
        listEl.appendChild(up);
      }
      var dirs = data.dirs || [];
      if (!dirs.length) {
        var e = document.createElement("div");
        e.className = "browse-empty";
        e.textContent = data.path ? "No subfolders here. You can still add this folder." : "No drives found.";
        listEl.appendChild(e);
      }
      dirs.forEach(function (f) {
        var row = document.createElement("button");
        row.className = "browse-row";
        row.innerHTML = FOLDER_SVG + '<span class="name">' + (f.name || f.path) + '</span>';
        row.addEventListener("click", function () { navigate(f.path); });
        listEl.appendChild(row);
      });
    }

    function navigate(path) {
      listEl.innerHTML = '<div class="browse-empty">Loading…</div>';
      fetch("/browse?path=" + encodeURIComponent(path || ""))
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function () { listEl.innerHTML = '<div class="browse-empty">Could not read that folder.</div>'; });
    }

    function open() { browseModal.hidden = false; navigate(""); }
    function close() { browseModal.hidden = true; }

    browseOpen.addEventListener("click", open);
    if (closeBtn) closeBtn.addEventListener("click", close);
    browseModal.addEventListener("click", function (e) { if (e.target === browseModal) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !browseModal.hidden) close(); });

    addBtn.addEventListener("click", function () {
      if (!selPath) return;
      addBtn.disabled = true; addBtn.textContent = "Adding…";
      var fd = new FormData(); fd.append("folder_path", selPath);
      fetch("/add_folder", { method: "POST", body: fd })
        .then(function () { location.href = "/?folder=" + encodeURIComponent(selPath); })
        .catch(function () { location.reload(); });
    });
  }

  // --- Watched progress bars on cards (home) ---
  if (grid) {
    var prog = loadProgress();
    grid.querySelectorAll(".card").forEach(function (c) {
      var id = c.getAttribute("data-id");
      var entry = prog[id];
      if (!entry || !entry.d) return;
      var ratio = entry.t / entry.d;
      if (ratio > 0.02 && ratio < 0.97) {
        c.classList.add("partial");
        var bar = c.querySelector(".watched__bar");
        if (bar) bar.style.width = (ratio * 100).toFixed(1) + "%";
      }
    });

    // --- "Continue watching" shelf (from local progress, most-recent first) ---
    var shelf = document.getElementById("continue-shelf");
    var shelfRow = document.getElementById("continue-row");
    if (shelf && shelfRow) {
      var resume = [];
      grid.querySelectorAll(".card").forEach(function (c) {
        var id = c.getAttribute("data-id"), e = prog[id];
        if (!e || !e.d) return;
        var ratio = e.t / e.d;
        if (ratio <= 0.02 || ratio >= 0.97) return;
        var img = c.querySelector(".thumb__img"), badge = c.querySelector(".badge");
        resume.push({
          id: id, ratio: ratio, ts: e.ts || 0,
          title: c.getAttribute("data-name") || "",
          thumb: img ? img.getAttribute("src") : "/thumb/" + id,
          dur: badge ? badge.textContent : "",
        });
      });
      resume.sort(function (a, b) { return b.ts - a.ts; });
      if (resume.length) {
        resume.forEach(function (it) {
          var a = document.createElement("a");
          a.className = "shelf-card";
          a.href = "/watch/" + it.id;
          a.innerHTML =
            '<span class="thumb"><img loading="lazy" src="' + it.thumb + '" alt="">' +
            '<span class="badge">' + it.dur + '</span>' +
            '<span class="resume"><span class="resume__bar" style="width:' +
            (it.ratio * 100).toFixed(1) + '%"></span></span></span>' +
            '<div class="st">' + it.title.replace(/</g, "&lt;") + '</div>';
          shelfRow.appendChild(a);
        });
        shelf.hidden = false;
      }
    }

    // --- Hover-to-play preview (YouTube-style), native formats only ---
    grid.querySelectorAll('.card[data-native="1"]').forEach(function (card) {
      var thumb = card.querySelector(".thumb");
      var id = card.getAttribute("data-id");
      var timer = null, vid = null;
      card.addEventListener("mouseenter", function () {
        timer = setTimeout(function () {
          vid = document.createElement("video");
          vid.className = "preview";
          vid.muted = true; vid.playsInline = true; vid.loop = true; vid.preload = "auto";
          vid.src = "/stream/" + id;
          thumb.appendChild(vid);
          card.classList.add("previewing");
          vid.addEventListener("loadeddata", function () {
            try { if (vid.duration && vid.duration > 15) vid.currentTime = Math.min(10, vid.duration * 0.15); } catch (e) {}
            vid.classList.add("show");
            vid.play().catch(function () {});
          });
        }, 550);
      });
      card.addEventListener("mouseleave", function () {
        clearTimeout(timer);
        if (vid) { try { vid.pause(); } catch (e) {} vid.remove(); vid = null; }
        card.classList.remove("previewing");
      });
    });
  }

  // --- Show in folder / Open in player ---
  // Run directly by the server when it's on the PC; when it runs in Docker the
  // server returns the host path and we pass it to the on-PC helper
  // (otube-helper.ps1, started by the launcher, listening on 127.0.0.1 only).
  var HELPER = "http://127.0.0.1:8701/";
  function note(msg) {
    var t = document.getElementById("note-toast");
    if (!t) { t = document.createElement("div"); t.id = "note-toast"; t.className = "note-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(note._t); note._t = setTimeout(function () { t.classList.remove("show"); }, 3500);
  }
  function hostAction(act, id, fallbackPath) {
    fetch("/" + act + "/" + id, { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { note(d.error || "Failed"); return; }
        if (!d.delegate) return;  // server handled it
        return fetch(HELPER + act, { method: "POST", body: d.delegate })
          .then(function (r) { if (!r.ok) throw new Error(); })
          .catch(function () {
            if (navigator.clipboard) navigator.clipboard.writeText(d.delegate);
            note("Helper not running (start oTube from its desktop icon). Path copied to clipboard.");
          });
      })
      .catch(function () { note("Failed"); });
  }

  // --- Per-video context menu (grid cards AND the watch page) ---
  var menu = document.getElementById("ctx-menu");
  if (menu) {
    var current = null; // { btn, card, id, name, path, watch }

    function closeMenu() {
      menu.hidden = true;
      if (current && current.btn) current.btn.classList.remove("open");
      current = null;
    }
    function openMenu(info) {
      current = info;
      info.btn.classList.add("open");
      menu.hidden = false;
      var r = info.btn.getBoundingClientRect();
      var mw = menu.offsetWidth || 220, mh = menu.offsetHeight || 260;
      var left = Math.min(r.right - mw, window.innerWidth - mw - 8);
      var top = r.bottom + 4;
      if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = top + "px";
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".card__more, .watch-more");
      if (btn) {
        e.preventDefault(); e.stopPropagation();
        if (current && current.btn === btn) { closeMenu(); return; }
        var src = btn.closest("[data-id]") || btn;  // card on home, the button itself on watch
        openMenu({
          btn: btn, card: btn.closest(".card"),
          id: src.getAttribute("data-id"),
          name: src.getAttribute("data-name") || "",
          path: src.getAttribute("data-path") || "",
          watch: !!btn.getAttribute("data-watch"),
        });
        return;
      }
      if (!menu.hidden && !menu.contains(e.target)) closeMenu();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });

    menu.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b || !current) return;
      var act = b.getAttribute("data-act");
      var id = current.id, name = current.name, path = current.path;
      var card = current.card, isWatch = current.watch;
      closeMenu();

      if (act === "save") {
        openSaveDialog(id, name);
      } else if (act === "pl-up" || act === "pl-down" || act === "pl-remove") {
        var pid = grid && grid.getAttribute("data-playlist");
        if (!pid) return;
        plPost(pid, act.slice(3), { vid: id }).then(function () { location.reload(); });
      } else if (act === "copy") {
        if (navigator.clipboard) navigator.clipboard.writeText(path);
      } else if (act === "reveal" || act === "open") {
        hostAction(act, id, path);
      } else if (act === "clear") {
        var p = loadProgress(); delete p[id]; saveProgress(p);
        if (card) {
          card.classList.remove("partial");
          var bar = card.querySelector(".watched__bar"); if (bar) bar.style.width = "0";
        }
      } else if (act === "rename") {
        var nn = window.prompt("Rename video to:", name);
        if (nn && nn.trim() && nn.trim() !== name) {
          var fd = new FormData(); fd.append("name", nn.trim());
          fetch("/rename/" + id, { method: "POST", body: fd })
            .then(function (res) { return res.json(); })
            .then(function (d) { if (d.ok) location.reload(); else alert("Rename failed: " + (d.error || "")); })
            .catch(function () { alert("Rename failed."); });
        }
      } else if (act === "delete") {
        if (window.confirm("Delete this video FROM DISK permanently?\n\n" + name)) {
          fetch("/delete/" + id, { method: "POST" })
            .then(function (res) { return res.json(); })
            .then(function (d) {
              if (!d.ok) { alert("Delete failed: " + (d.error || "")); return; }
              if (isWatch) window.location.href = "/"; else if (card) card.remove();
            })
            .catch(function () { alert("Delete failed."); });
        }
      }
    });
  }

  // --- Playlists ---
  function plPost(pid, action, data) {
    var fd = new FormData();
    Object.keys(data || {}).forEach(function (k) { fd.append(k, data[k]); });
    return fetch("/api/playlists/" + pid + "/" + action, { method: "POST", body: fd })
      .then(function (r) { return r.json(); });
  }
  function plCreate(name, vid) {
    var fd = new FormData(); fd.append("name", name);
    if (vid) fd.append("vid", vid);
    return fetch("/api/playlists", { method: "POST", body: fd }).then(function (r) { return r.json(); });
  }
  function esc(t) {
    return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }

  // "Save to playlist" dialog: tick/untick playlists, or create a new one.
  var saveModal = null, saveDirty = false;
  function closeSave() {
    saveModal.hidden = true;
    if (saveDirty && document.getElementById("sidebar")) location.reload();  // refresh counts
    saveDirty = false;
  }
  function openSaveDialog(vid, title) {
    if (!saveModal) {
      saveModal = document.createElement("div");
      saveModal.className = "modal-overlay";
      saveModal.innerHTML =
        '<div class="modal modal--sm">' +
        '<div class="modal__head"><span>Save to playlist</span>' +
        '<button class="modal__close" data-close aria-label="Close">✕</button></div>' +
        '<div class="pl-video"></div>' +
        '<div class="pl-list"></div>' +
        '<form class="pl-new"><input type="text" placeholder="New playlist name" maxlength="100">' +
        '<button type="submit">Create</button></form></div>';
      document.body.appendChild(saveModal);
      saveModal.addEventListener("click", function (e) {
        if (e.target === saveModal || e.target.closest("[data-close]")) closeSave();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && saveModal && !saveModal.hidden) closeSave();
      });
    }
    var list = saveModal.querySelector(".pl-list");
    var form = saveModal.querySelector(".pl-new");
    var input = form.querySelector("input");
    saveModal.querySelector(".pl-video").textContent = title || "";
    input.value = "";

    function load() {
      list.innerHTML = '<div class="browse-empty">Loading…</div>';
      fetch("/api/playlists?vid=" + encodeURIComponent(vid))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var pls = d.playlists || [];
          if (!pls.length) {
            list.innerHTML = '<div class="browse-empty">No playlists yet. Create one below.</div>';
            return;
          }
          list.innerHTML = "";
          pls.forEach(function (p) {
            var row = document.createElement("label");
            row.className = "pl-row";
            row.innerHTML = '<input type="checkbox"' + (p.has ? " checked" : "") + '>' +
              '<span class="pl-name">' + esc(p.name) + '</span>' +
              '<span class="pl-count">' + p.count + '</span>';
            row.querySelector("input").addEventListener("change", function (e) {
              saveDirty = true;
              plPost(p.id, e.target.checked ? "add" : "remove", { vid: vid });
            });
            list.appendChild(row);
          });
        });
    }
    form.onsubmit = function (e) {
      e.preventDefault();
      var n = input.value.trim();
      if (!n) return;
      saveDirty = true;
      plCreate(n, vid).then(function () { input.value = ""; load(); });
    };
    saveModal.hidden = false;
    load();
  }

  var newPl = document.getElementById("new-playlist");
  if (newPl) {
    newPl.addEventListener("click", function () {
      var n = window.prompt("New playlist name:");
      if (n && n.trim()) plCreate(n.trim()).then(function (d) {
        if (d.ok) location.href = "/?playlist=" + d.id;
      });
    });
  }
  var plRename = document.getElementById("pl-rename");
  if (plRename) {
    plRename.addEventListener("click", function () {
      var n = window.prompt("Rename playlist:", plRename.getAttribute("data-name"));
      if (n && n.trim()) plPost(plRename.getAttribute("data-pid"), "rename", { name: n.trim() })
        .then(function () { location.reload(); });
    });
  }
  var plDelete = document.getElementById("pl-delete");
  if (plDelete) {
    plDelete.addEventListener("click", function () {
      if (window.confirm('Delete the playlist "' + plDelete.getAttribute("data-name") + '"?\n\nThe videos themselves are not touched.'))
        plPost(plDelete.getAttribute("data-pid"), "delete").then(function () { location.href = "/"; });
    });
  }

  // Watch-page playback lives in player.js.
})();
