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

      if (act === "copy") {
        if (navigator.clipboard) navigator.clipboard.writeText(path);
      } else if (act === "reveal") {
        fetch("/reveal/" + id, { method: "POST" });
      } else if (act === "open") {
        fetch("/open/" + id, { method: "POST" });
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

  // --- Watch page: resume, save progress, autoplay next ---
  var player = document.getElementById("player");
  if (player) {
    var id = player.getAttribute("data-id");
    var next = player.getAttribute("data-next");
    var prog2 = loadProgress();

    function startPlayback() {
      // Try to autoplay WITH sound. If the browser blocks it (no engagement yet),
      // DON'T force-mute — just leave it paused with the play button, so one click
      // plays it with sound. Never auto-mute. (As the browser learns you use this
      // site, it grants sound-autoplay on its own.)
      player.muted = false;
      var p = player.play();
      if (p && p.catch) { p.catch(function () { /* blocked: wait for a click → sound */ }); }
    }

    player.addEventListener("loadedmetadata", function () {
      var entry = prog2[id];
      if (entry && entry.t > 5 && player.duration && entry.t < player.duration - 10) {
        try { player.currentTime = entry.t; } catch (e) {}
      }
      startPlayback();
    });

    var last = 0;
    function store() {
      if (!player.duration) return;
      var p = loadProgress();
      p[id] = { t: player.currentTime, d: player.duration, ts: Date.now() };
      saveProgress(p);
    }
    player.addEventListener("timeupdate", function () {
      if (player.currentTime - last > 5) { last = player.currentTime; store(); }
    });
    player.addEventListener("pause", store);
    window.addEventListener("beforeunload", store);

    player.addEventListener("ended", function () {
      var p = loadProgress();
      if (player.duration) { p[id] = { t: player.duration, d: player.duration, ts: Date.now() }; saveProgress(p); }
      if (next) window.location.href = next;
    });

    // --- Keyboard shortcuts + Picture-in-Picture (watch page) ---
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      var k = e.key;
      if (k === " " || k === "k") { e.preventDefault(); player.paused ? player.play() : player.pause(); }
      else if (k === "ArrowRight") { e.preventDefault(); player.currentTime += 5; }
      else if (k === "ArrowLeft") { e.preventDefault(); player.currentTime -= 5; }
      else if (k === "l") { player.currentTime += 10; }
      else if (k === "j") { player.currentTime -= 10; }
      else if (k === "ArrowUp") { e.preventDefault(); player.volume = Math.min(1, player.volume + 0.1); }
      else if (k === "ArrowDown") { e.preventDefault(); player.volume = Math.max(0, player.volume - 0.1); }
      else if (k === "m") { player.muted = !player.muted; }
      else if (k === "f") { if (document.fullscreenElement) document.exitFullscreen(); else player.requestFullscreen && player.requestFullscreen(); }
      else if (k === "p") {
        if (document.pictureInPictureElement) document.exitPictureInPicture();
        else if (player.requestPictureInPicture) player.requestPictureInPicture().catch(function () {});
      } else if (k >= "0" && k <= "9") {
        if (player.duration) player.currentTime = player.duration * (parseInt(k, 10) / 10);
      }
    });
  }
})();
