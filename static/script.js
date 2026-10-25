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

  // --- Confirm dialog (replaces window.confirm) ---
  // opts: { title, text, confirm, danger } -> Promise<boolean>
  function confirmDialog(opts) {
    return new Promise(function (resolve) {
      var prev = document.activeElement;
      var ov = document.createElement("div");
      ov.className = "modal-overlay pin-overlay";
      ov.innerHTML =
        '<div class="modal pin-modal confirm-modal" role="alertdialog" aria-modal="true" aria-labelledby="cf-t" aria-describedby="cf-d">' +
        '<div class="pin-icon' + (opts.danger ? ' danger' : '') + '">' + (opts.icon || "") + '</div>' +
        '<h2 class="pin-title" id="cf-t"></h2><p class="pin-text" id="cf-d"></p>' +
        '<div class="pin-actions confirm-actions"><span class="pin-spacer"></span>' +
        '<button type="button" class="pin-btn cf-no">Cancel</button>' +
        '<button type="button" class="pin-btn pin-ok cf-yes' + (opts.danger ? '' : ' cf-safe') + '"></button></div></div>';
      ov.querySelector("#cf-t").textContent = opts.title;
      ov.querySelector("#cf-d").textContent = opts.text || "";
      ov.querySelector(".cf-yes").textContent = opts.confirm || "OK";
      document.body.appendChild(ov);
      function done(v) {
        ov.remove(); document.removeEventListener("keydown", key, true);
        if (prev && prev.focus) prev.focus();
        resolve(v);
      }
      function key(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
        else if (e.key === "Tab") {  // keep focus inside
          var b = ov.querySelectorAll("button"), f = b[0], l = b[b.length - 1];
          if (e.shiftKey && document.activeElement === f) { e.preventDefault(); l.focus(); }
          else if (!e.shiftKey && document.activeElement === l) { e.preventDefault(); f.focus(); }
        }
      }
      document.addEventListener("keydown", key, true);
      ov.querySelector(".cf-no").addEventListener("click", function () { done(false); });
      ov.querySelector(".cf-yes").addEventListener("click", function () { done(true); });
      ov.addEventListener("mousedown", function (e) { if (e.target === ov) done(false); });
      // destructive actions focus Cancel by default (safer); others focus the action
      setTimeout(function () { ov.querySelector(opts.danger ? ".cf-no" : ".cf-yes").focus(); }, 20);
    });
  }
  window.otubeConfirm = confirmDialog;
  var TRASH = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';
  var FOLDER_X = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8zm4.6 6 1.4 1.4-1.6 1.6 1.6 1.6-1.4 1.4-1.6-1.6-1.6 1.6-1.4-1.4 1.6-1.6-1.6-1.6 1.4-1.4 1.6 1.6z"/></svg>';
  var LIST_X = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M3 6h12v2H3zm0 4h12v2H3zm0 4h8v2H3zm11.4 1.4L16 13.8l2 2 2-2 1.4 1.4-2 2 2 2-1.4 1.4-2-2-2 2-1.4-1.4 2-2z"/></svg>';

  // --- Sidebar toggle ---
  var menuBtn = document.getElementById("menu-toggle");
  var sidebar = document.getElementById("sidebar");
  if (menuBtn && sidebar && sidebar.classList.contains("drawer")) {
    // Watch page: the guide slides in over the page (like YouTube), with a scrim.
    var scrim = document.getElementById("drawer-scrim");
    var setDrawer = function (open) {
      sidebar.classList.toggle("hidden", !open);
      if (scrim) scrim.hidden = !open;
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { var f = sidebar.querySelector("a, button"); if (f) f.focus(); } else menuBtn.focus();
    };
    menuBtn.addEventListener("click", function () { setDrawer(sidebar.classList.contains("hidden")); });
    if (scrim) scrim.addEventListener("click", function () { setDrawer(false); });
    var dc = document.getElementById("drawer-close");
    if (dc) dc.addEventListener("click", function () { setDrawer(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !sidebar.classList.contains("hidden")) { e.stopImmediatePropagation(); setDrawer(false); }
    }, true);
  } else if (menuBtn && sidebar) {
    menuBtn.addEventListener("click", function () { sidebar.classList.toggle("hidden"); });
  }

  // --- Chip rows: bring the active chip into view (rows scroll sideways) ---
  document.querySelectorAll(".chips .chip.active").forEach(function (c) {
    var row = c.closest(".chips");
    if (c.offsetLeft + c.offsetWidth > row.clientWidth) row.scrollLeft = c.offsetLeft - 24;
  });

  // --- Folder tree in the sidebar: expand/collapse, remembered per folder ---
  var TREE_KEY = "otube.tree";
  var openSet = {};
  try { openSet = JSON.parse(localStorage.getItem(TREE_KEY) || "{}"); } catch (e) {}
  function setNode(node, open) {
    node.classList.toggle("open", open);
    var t = node.querySelector(":scope > .nav-item > .tree-tog");
    if (t && t.tagName === "BUTTON") t.setAttribute("aria-expanded", open ? "true" : "false");
  }
  document.querySelectorAll(".tree-node").forEach(function (n) {
    if (openSet[n.getAttribute("data-key")]) setNode(n, true);
  });
  // open the path down to the selected folder
  var act = document.querySelector(".sidebar .nav-item.sub.active");
  if (act) {
    for (var n = act.parentElement.parentElement.closest(".tree-node"); n; n = n.parentElement.closest(".tree-node")) setNode(n, true);
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest(".tree-tog");
    if (!t || t.tagName !== "BUTTON") return;
    e.preventDefault(); e.stopPropagation();
    var node = t.closest(".tree-node"), open = !node.classList.contains("open");
    setNode(node, open);
    var k = node.getAttribute("data-key");
    if (open) openSet[k] = 1; else delete openSet[k];
    try { localStorage.setItem(TREE_KEY, JSON.stringify(openSet)); } catch (err) {}
  });

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
      var sub = sortSel.getAttribute("data-sub") || "";
      if (sub) url += "&sub=" + encodeURIComponent(sub);
      if (sortSel.value === "random") url += "&seed=" + Math.floor(Math.random() * 1e6);
      window.location.href = url;
    });
  }
  var reshuffle = document.getElementById("reshuffle");
  if (reshuffle) {
    reshuffle.addEventListener("click", function () {
      var u = new URL(location.href);
      u.searchParams.set("seed", Math.floor(Math.random() * 1e6));
      location.href = u.toString();
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

  // --- Multi-select (Google Photos / Gmail style) ---
  // Hover a card -> checkbox. Once anything is selected, clicking a card toggles it,
  // Shift+click selects a range, Ctrl/Cmd+A selects all visible, Esc clears.
  var selbar = document.getElementById("selbar");
  if (grid && selbar) {
    var selected = [], lastIdx = -1;
    var countEl = document.getElementById("sel-count");
    function visibleCards() {
      return [].slice.call(grid.querySelectorAll(".card")).filter(function (c) { return c.style.display !== "none"; });
    }
    function sync() {
      grid.classList.toggle("selecting", selected.length > 0);
      selbar.hidden = selected.length === 0;
      countEl.textContent = selected.length + " selected";
      grid.querySelectorAll(".card").forEach(function (c) {
        var on = selected.indexOf(c.getAttribute("data-id")) !== -1;
        c.classList.toggle("selected", on);
        var b = c.querySelector(".card__check");
        if (b) b.setAttribute("aria-pressed", on ? "true" : "false");
      });
    }
    function setSel(card, on) {
      var id = card.getAttribute("data-id"), i = selected.indexOf(id);
      if (on && i === -1) selected.push(id);
      if (!on && i !== -1) selected.splice(i, 1);
    }
    function clearSel() { selected = []; lastIdx = -1; sync(); }
    function toggleCard(card, shift) {
      var cards = visibleCards(), idx = cards.indexOf(card);
      var on = selected.indexOf(card.getAttribute("data-id")) === -1;
      if (shift && lastIdx !== -1) {
        var a = Math.min(lastIdx, idx), b = Math.max(lastIdx, idx);
        for (var i = a; i <= b; i++) setSel(cards[i], true);
      } else setSel(card, on);
      lastIdx = idx;
      sync();
    }
    grid.addEventListener("click", function (e) {
      var card = e.target.closest(".card");
      if (!card || e.target.closest(".card__more")) return;
      if (e.target.closest(".card__check") || selected.length) {
        e.preventDefault(); e.stopPropagation();
        toggleCard(card, e.shiftKey);
      }
    }, true);
    document.getElementById("sel-clear").addEventListener("click", clearSel);
    document.getElementById("sel-all").addEventListener("click", function () {
      visibleCards().forEach(function (c) { setSel(c, true); }); sync();
    });
    document.getElementById("sel-save").addEventListener("click", function () {
      openSaveDialog(selected.slice(), "", function (msg) { note(msg + " (" + selected.length + " video" + (selected.length === 1 ? "" : "s") + ")"); });
    });
    var selRemove = document.getElementById("sel-remove");
    if (selRemove) selRemove.addEventListener("click", function () {
      var n = selected.length;
      confirmDialog({ title: "Remove " + n + " video" + (n === 1 ? "" : "s") + " from this playlist?",
                      text: "The video files themselves are not touched.", confirm: "Remove", danger: true, icon: LIST_X })
        .then(function (ok) {
          if (ok) plPost(selRemove.getAttribute("data-pid"), "remove", { vid: selected }).then(function () { location.reload(); });
        });
    });
    document.addEventListener("keydown", function (e) {
      var t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if (document.querySelector(".modal-overlay:not([hidden])")) return;
      if (e.key === "Escape" && selected.length) { clearSel(); }
      else if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A")) {
        e.preventDefault();
        visibleCards().forEach(function (c) { setSel(c, true); }); sync();
      }
    });
    // Search filters cards: drop hidden ones from the selection
    if (search) search.addEventListener("input", function () {
      var vis = visibleCards().map(function (c) { return c.getAttribute("data-id"); });
      selected = selected.filter(function (id) { return vis.indexOf(id) !== -1; });
      sync();
    });
  }

  // --- Family / Private mode ---
  function postForm(url, data) {
    var fd = new FormData();
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, { method: "POST", body: fd }).then(function (r) { return r.json(); });
  }

  // PIN dialog. opts: { title, text, setup (new + confirm), allowRemove, submitLabel,
  // onSubmit(pin) -> Promise<{ok, error, wait}>, onRemove() -> Promise }.
  var LOCK_SVG = '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 8V7a3 3 0 0 1 6 0v3H9zm3 4a2 2 0 0 1 1 3.7V19h-2v-1.3A2 2 0 0 1 12 14z"/></svg>', EYE_SVG = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M12 5C6.5 5 2.7 9.4 1.5 12c1.2 2.6 5 7 10.5 7s9.3-4.4 10.5-7C21.3 9.4 17.5 5 12 5zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm0-6a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>';
  function pinDialog(opts) {
    var prevFocus = document.activeElement;
    var ov = document.createElement("div");
    ov.className = "modal-overlay pin-overlay";
    ov.innerHTML =
      '<div class="modal pin-modal" role="dialog" aria-modal="true" aria-labelledby="pin-title">' +
      '  <button class="modal__close pin-x" type="button" aria-label="Close">✕</button>' +
      '  <div class="pin-icon">' + LOCK_SVG + '</div>' +
      '  <h2 class="pin-title" id="pin-title"></h2>' +
      '  <p class="pin-text"></p>' +
      '  <form class="pin-form" novalidate>' +
      '    <label class="pin-field"><span class="pin-label">' + (opts.setup ? "New PIN" : "PIN") + '</span>' +
      '      <span class="pin-wrap"><input class="pin-input" type="password" inputmode="numeric" pattern="[0-9]*"' +
      '        maxlength="8" autocomplete="off" spellcheck="false">' +
      '        <button type="button" class="pin-eye" aria-label="Show PIN" title="Show PIN">' + EYE_SVG + '</button></span></label>' +
      (opts.setup ?
      '    <label class="pin-field"><span class="pin-label">Confirm PIN</span>' +
      '      <span class="pin-wrap"><input class="pin-input" type="password" inputmode="numeric" pattern="[0-9]*"' +
      '        maxlength="8" autocomplete="off" spellcheck="false"></span></label>' : '') +
      '    <div class="pin-error" role="alert" aria-live="assertive"></div>' +
      '    <div class="pin-actions">' +
      (opts.allowRemove ? '<button type="button" class="pin-btn pin-remove">Remove PIN</button>' : '') +
      '      <span class="pin-spacer"></span>' +
      '      <button type="button" class="pin-btn pin-cancel">Cancel</button>' +
      '      <button type="submit" class="pin-btn pin-ok" disabled></button>' +
      '    </div>' +
      '  </form>' +
      '</div>';
    document.body.appendChild(ov);
    var modal = ov.querySelector(".pin-modal");
    var inputs = [].slice.call(ov.querySelectorAll(".pin-input"));
    var okBtn = ov.querySelector(".pin-ok"), errEl = ov.querySelector(".pin-error");
    ov.querySelector(".pin-title").textContent = opts.title;
    ov.querySelector(".pin-text").textContent = opts.text || "";
    okBtn.textContent = opts.submitLabel || "OK";

    function close() {
      ov.remove();
      document.removeEventListener("keydown", onKey, true);
      if (prevFocus && prevFocus.focus) prevFocus.focus();
    }
    function error(msg) {
      errEl.textContent = msg;
      modal.classList.remove("shake"); void modal.offsetWidth; modal.classList.add("shake");
    }
    function valid() {
      var a = inputs[0].value;
      if (!/^\d{4,8}$/.test(a)) return false;
      return !opts.setup || inputs[1].value.length > 0;
    }
    inputs.forEach(function (inp) {
      inp.addEventListener("input", function () {
        inp.value = inp.value.replace(/\D/g, "").slice(0, 8);  // digits only
        errEl.textContent = "";
        okBtn.disabled = !valid();
      });
    });
    ov.querySelector(".pin-eye").addEventListener("click", function () {
      var show = inputs[0].type === "password";
      inputs.forEach(function (i) { i.type = show ? "text" : "password"; });
      this.classList.toggle("on", show);
      this.setAttribute("aria-label", show ? "Hide PIN" : "Show PIN");
      this.title = show ? "Hide PIN" : "Show PIN";
    });
    ov.querySelector(".pin-x").addEventListener("click", close);
    ov.querySelector(".pin-cancel").addEventListener("click", close);
    ov.addEventListener("mousedown", function (e) { if (e.target === ov) close(); });
    var rm = ov.querySelector(".pin-remove");
    if (rm) rm.addEventListener("click", function () {
      rm.disabled = true;
      opts.onRemove().then(close);
    });
    // Esc closes; Tab stays inside the dialog.
    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if (e.key !== "Tab") return;
      var f = [].slice.call(modal.querySelectorAll("button:not([disabled]), input"));
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", onKey, true);

    ov.querySelector(".pin-form").addEventListener("submit", function (e) {
      e.preventDefault();
      if (!valid()) { error("PIN must be 4 to 8 digits."); return; }
      if (opts.setup && inputs[0].value !== inputs[1].value) {
        error("PINs don't match."); inputs[1].value = ""; inputs[1].focus(); okBtn.disabled = true; return;
      }
      okBtn.disabled = true;
      okBtn.classList.add("busy");
      opts.onSubmit(inputs[0].value).then(function (d) {
        okBtn.classList.remove("busy");
        if (d && d.ok) { close(); return; }
        error((d && d.error) || "Something went wrong.");
        inputs.forEach(function (i) { i.value = ""; });
        if (d && d.wait) {  // locked out: count down, then re-enable
          inputs.forEach(function (i) { i.disabled = true; });
          var n = d.wait, t = setInterval(function () {
            n -= 1;
            errEl.textContent = "Too many tries. Wait " + n + "s.";
            if (n <= 0) {
              clearInterval(t); errEl.textContent = "";
              inputs.forEach(function (i) { i.disabled = false; });
              inputs[0].focus();
            }
          }, 1000);
        } else inputs[0].focus();
      }).catch(function () { okBtn.classList.remove("busy"); okBtn.disabled = false; error("Network error."); });
    });
    setTimeout(function () { inputs[0].focus(); }, 30);
  }

  var modeBtn = document.getElementById("mode-btn");
  if (modeBtn) {
    modeBtn.addEventListener("click", function () {
      if (modeBtn.getAttribute("data-private") === "1") {
        postForm("/mode", { private: "0" }).then(function () { location.href = "/"; });
        return;
      }
      if (modeBtn.getAttribute("data-pin") !== "1") {
        postForm("/mode", { private: "1" }).then(function () { location.reload(); });
        return;
      }
      pinDialog({
        title: "Enter PIN",
        text: "Unlock Private mode to show private folders.",
        submitLabel: "Unlock",
        onSubmit: function (pin) {
          return postForm("/mode", { private: "1", pin: pin }).then(function (d) {
            if (d.ok) location.reload();
            return d;
          });
        },
      });
    });
  }
  // --- Folder ⋮ menu (sidebar folders and subfolders) ---
  var fmenu = null, fcur = null;
  var PRIVATE_ON = !!document.querySelector('.mode-btn.on');
  function fItem(act, label, cls) {
    return '<button type="button" role="menuitem" data-fact="' + act + '"' + (cls ? ' class="' + cls + '"' : '') + '>' + label + '</button>';
  }
  function closeFolderMenu() {
    if (!fmenu || fmenu.hidden) return;
    fmenu.hidden = true;
    if (fcur) { fcur.btn.classList.remove("open"); fcur.btn.setAttribute("aria-expanded", "false"); }
  }
  function openFolderMenu(btn) {
    var row = btn.closest(".nav-item, .chip-wrap");
    if (row.hasAttribute("data-pid")) { openPlaylistMenu(btn, row); return; }
    var isSub = !!row.getAttribute("data-sub");
    fcur = { btn: btn, row: row, folder: row.getAttribute("data-folder"), sub: row.getAttribute("data-sub") || "",
             host: row.getAttribute("data-host"), name: row.getAttribute("data-name") };
    ensureMenu();
    var h = fItem("play", "▶&nbsp; Play all") + fItem("shuffle", "🔀&nbsp; Shuffle play") +
      '<div class="ctx-sep"></div>' +
      fItem("reveal", "Show in Explorer") + fItem("copy", "Copy path");
    if (!isSub) {
      var priv = row.getAttribute("data-private") === "1";
      h += '<div class="ctx-sep"></div>' +
        fItem(priv ? "public" : "private", priv ? "Move to Family mode (make public)" : "Move to Private mode…");
      h += '<div class="ctx-sep"></div>' +
        (row.hasAttribute("data-nomove") || row.getAttribute("data-first") === "1" ? "" : fItem("up", "Move up")) +
        (row.hasAttribute("data-nomove") || row.getAttribute("data-last") === "1" ? "" : fItem("down", "Move down")) +
        '<div class="ctx-sep"></div>' + fItem("remove", "Remove from oTube…", "danger");
    }
    showMenuAt(btn, h);
  }
  function ensureMenu() {
    if (fmenu) return;
    fmenu = document.createElement("div");
    fmenu.className = "ctx-menu folder-menu"; fmenu.setAttribute("role", "menu"); fmenu.hidden = true;
    document.body.appendChild(fmenu);
    fmenu.addEventListener("click", onFolderAction);
    fmenu.addEventListener("keydown", function (e) {
      var items = [].slice.call(fmenu.querySelectorAll("button:not([disabled])"));
      var i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); closeFolderMenu(); fcur.btn.focus(); }
      else if (e.key === "Tab") closeFolderMenu();
    });
  }
  function showMenuAt(btn, html) {
    fmenu.innerHTML = html.replace(/(<div class="ctx-sep"><\/div>)+$/, "");
    fmenu.hidden = false;
    btn.classList.add("open"); btn.setAttribute("aria-expanded", "true");
    var r = btn.getBoundingClientRect(), mw = fmenu.offsetWidth, mh = fmenu.offsetHeight;
    var left = Math.min(r.left, window.innerWidth - mw - 8);
    var top = r.bottom + 4;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
    fmenu.style.left = Math.max(8, left) + "px"; fmenu.style.top = top + "px";
    var first = fmenu.querySelector("button"); if (first) first.focus();
  }
  function openPlaylistMenu(btn, row) {
    ensureMenu();
    fcur = { btn: btn, row: row, pid: row.getAttribute("data-pid"), name: row.getAttribute("data-name") };
    var empty = row.getAttribute("data-count") === "0", liked = fcur.pid === "liked";
    var h = (empty ? "" : fItem("pl-play", "▶&nbsp; Play all") + fItem("pl-shuffle", "🔀&nbsp; Shuffle play") +
      '<div class="ctx-sep"></div>') +
      (liked ? "" : fItem("pl-rename", "Rename…")) +
      (row.getAttribute("data-first") === "1" ? "" : fItem("pl-up", "Move up")) +
      (row.getAttribute("data-last") === "1" ? "" : fItem("pl-down", "Move down")) +
      (liked ? "" : '<div class="ctx-sep"></div>' + fItem("pl-delete", "Delete playlist…", "danger"));
    showMenuAt(btn, h);
  }
  function onFolderAction(e) {
    var b = e.target.closest("[data-fact]"); if (!b || !fcur) return;
    var act = b.getAttribute("data-fact"), c = fcur;
    closeFolderMenu();
    if (act.indexOf("pl-") === 0) { onPlaylistAction(act, c); return; }
    var q = "folder=" + encodeURIComponent(c.folder) + (c.sub ? "&sub=" + encodeURIComponent(c.sub) : "");
    if (act === "play") location.href = "/folder_play?" + q;
    else if (act === "shuffle") location.href = "/folder_play?" + q + "&shuffle=1";
    else if (act === "copy") {
      (navigator.clipboard ? navigator.clipboard.writeText(c.host) : Promise.reject())
        .then(function () { note("Path copied"); }, function () { window.prompt("Copy this path:", c.host); });
    } else if (act === "reveal") {
      fetch(HELPER + "reveal", { method: "POST", body: c.host })
        .then(function (r) { if (!r.ok) throw new Error(); })
        .catch(function () {
          if (navigator.clipboard) navigator.clipboard.writeText(c.host);
          note("Helper not running (start oTube from its desktop icon). Path copied to clipboard.");
        });
    } else if (act === "public") {
      postForm("/folder_private", { folder_path: c.folder, private: "0" }).then(function () { location.reload(); });
    } else if (act === "private") {
      var leaving = !PRIVATE_ON;
      (leaving ? confirmDialog({
        title: 'Move "' + c.name + '" to Private mode?',
        text: "It will disappear from Family mode and only show in Private mode" +
              (document.querySelector('#mode-btn[data-pin="1"]') ? " (PIN needed)." : ".") +
              " You can move it back from Private mode.",
        confirm: "Move to Private", icon: '<svg viewBox="0 0 24 24" width="28" height="28" aria-hidden="true"><path fill="currentColor" d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5zm-3 8V7a3 3 0 0 1 6 0v3H9z"/></svg>'
      }) : Promise.resolve(true)).then(function (ok) {
        if (!ok) return;
        postForm("/folder_private", { folder_path: c.folder, private: "1" }).then(function () {
          var u = new URL(location.href);
          if (leaving && u.searchParams.get("folder") === c.folder) location.href = "/"; else location.reload();
        });
      });
    } else if (act === "up" || act === "down") {
      postForm("/folder_move", { folder_path: c.folder, dir: act }).then(function () { location.reload(); });
    } else if (act === "remove") {
      confirmDialog({ title: 'Remove "' + c.name + '" from oTube?',
                      text: "The folder and its videos stay on your disk. It just won't be listed here anymore. You can add it back any time.",
                      confirm: "Remove folder", danger: true, icon: FOLDER_X })
        .then(function (ok) {
          if (!ok) return;
          var fd = new FormData(); fd.append("folder_path", c.folder);
          fetch("/remove_folder", { method: "POST", body: fd }).then(function () {
            var u = new URL(location.href);
            if (u.searchParams.get("folder") === c.folder) location.href = "/"; else location.reload();
          });
        });
    }
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".row-more");
    if (btn) {
      e.preventDefault(); e.stopPropagation();
      if (fmenu && !fmenu.hidden && fcur && fcur.btn === btn) closeFolderMenu(); else openFolderMenu(btn);
      return;
    }
    if (fmenu && !fmenu.hidden && !fmenu.contains(e.target)) closeFolderMenu();
  });
  function onPlaylistAction(act, c) {
    var here = new URL(location.href).searchParams.get("playlist") === c.pid ||
               new URL(location.href).searchParams.get("list") === c.pid;
    if (act === "pl-play") location.href = "/playlist_play?pid=" + encodeURIComponent(c.pid);
    else if (act === "pl-shuffle") location.href = "/playlist_play?pid=" + encodeURIComponent(c.pid) + "&shuffle=1";
    else if (act === "pl-up" || act === "pl-down") {
      plPost(c.pid, act === "pl-up" ? "listup" : "listdown").then(function () { location.reload(); });
    } else if (act === "pl-rename") {
      var n = window.prompt("Rename playlist:", c.name);
      if (n && n.trim() && n.trim() !== c.name) plPost(c.pid, "rename", { name: n.trim() }).then(function () { location.reload(); });
    } else if (act === "pl-delete") {
      confirmDialog({ title: 'Delete the playlist "' + c.name + '"?', text: "The videos themselves are not touched.",
                      confirm: "Delete playlist", danger: true, icon: LIST_X })
        .then(function (ok) {
          if (ok) plPost(c.pid, "delete").then(function () { if (here) location.href = "/"; else location.reload(); });
        });
    }
  }
  // Right-click (or the keyboard menu key) on a folder chip opens the same menu
  document.addEventListener("contextmenu", function (e) {
    var w = e.target.closest(".chip-wrap");
    if (!w) return;
    var b = w.querySelector(".chip-more");
    if (!b) return;
    e.preventDefault();
    openFolderMenu(b);
  });
  window.addEventListener("resize", closeFolderMenu);
  var sbar = document.getElementById("sidebar");
  if (sbar) sbar.addEventListener("scroll", closeFolderMenu);
  var pinBtn = document.getElementById("pin-btn");
  if (pinBtn) {
    pinBtn.addEventListener("click", function () {
      var hasPin = modeBtn && modeBtn.getAttribute("data-pin") === "1";
      pinDialog({
        title: hasPin ? "Change PIN" : "Set a PIN",
        text: "Needed to switch to Private mode. Use 4 to 8 digits.",
        setup: true,
        allowRemove: hasPin,
        submitLabel: "Save PIN",
        onSubmit: function (pin) {
          return postForm("/pin", { pin: pin }).then(function (d) {
            if (d.ok) location.reload();
            return d;
          });
        },
        onRemove: function () {
          return postForm("/pin", { pin: "" }).then(function () { location.reload(); });
        },
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
    var bar = document.getElementById("selbar");
    t.classList.toggle("raised", !!(bar && !bar.hidden));  // sit above the selection bar
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
        confirmDialog({ title: "Delete this video from disk?",
                        text: name + "\n\nThe file is permanently deleted from your computer. This can't be undone.",
                        confirm: "Delete forever", danger: true, icon: TRASH }).then(function (ok) {
          if (!ok) return;
          fetch("/delete/" + id, { method: "POST" })
            .then(function (res) { return res.json(); })
            .then(function (d) {
              if (!d.ok) { alert("Delete failed: " + (d.error || "")); return; }
              if (isWatch) window.location.href = "/"; else if (card) card.remove();
            })
            .catch(function () { alert("Delete failed."); });
        });
      }
    });
  }

  // --- Playlists ---
  function plPost(pid, action, data) {
    var fd = new FormData();
    Object.keys(data || {}).forEach(function (k) {
      [].concat(data[k]).forEach(function (v) { fd.append(k, v); });  // arrays -> repeated fields
    });
    return fetch("/api/playlists/" + pid + "/" + action, { method: "POST", body: fd })
      .then(function (r) { return r.json(); });
  }
  function plCreate(name, vid) {
    var fd = new FormData(); fd.append("name", name);
    [].concat(vid || []).forEach(function (v) { fd.append("vid", v); });
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
  // vids: one id or an array; title: label shown under the heading.
  function openSaveDialog(vids, title, onDone) {
    vids = [].concat(vids);
    var n = vids.length;
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
        if (e.key === "Escape" && saveModal && !saveModal.hidden) { e.stopImmediatePropagation(); closeSave(); }
      }, true);
    }
    saveModal.onDone = onDone || null;
    var list = saveModal.querySelector(".pl-list");
    var form = saveModal.querySelector(".pl-new");
    var input = form.querySelector("input");
    saveModal.querySelector(".pl-video").textContent = n > 1 ? n + " videos selected" : (title || "");
    input.value = "";

    function load() {
      list.innerHTML = '<div class="browse-empty">Loading…</div>';
      fetch("/api/playlists?" + vids.map(function (v) { return "vid=" + encodeURIComponent(v); }).join("&"))
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
            row.innerHTML = '<input type="checkbox">' +
              '<span class="pl-name">' + esc(p.name) + '</span>' +
              '<span class="pl-count">' + (n > 1 && p.has && p.has < n ? p.has + " of " + n + " · " : "") + p.count + '</span>';
            var cb = row.querySelector("input");
            cb.checked = p.has === n;
            cb.indeterminate = p.has > 0 && p.has < n;  // some of the selection already in it
            cb.addEventListener("change", function () {
              saveDirty = true;
              var add = cb.checked;
              plPost(p.id, add ? "add" : "remove", { vid: vids }).then(function () {
                if (saveModal.onDone) saveModal.onDone(add ? "Added to " + p.name : "Removed from " + p.name);
                load();
              });
            });
            list.appendChild(row);
          });
        });
    }
    form.onsubmit = function (e) {
      e.preventDefault();
      var name = input.value.trim();
      if (!name) return;
      saveDirty = true;
      plCreate(name, vids).then(function () {
        input.value = "";
        if (saveModal.onDone) saveModal.onDone("Created " + name);
        load();
      });
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
      confirmDialog({ title: 'Delete the playlist "' + plDelete.getAttribute("data-name") + '"?',
                      text: "The videos themselves are not touched.", confirm: "Delete playlist", danger: true, icon: LIST_X })
        .then(function (ok) { if (ok) plPost(plDelete.getAttribute("data-pid"), "delete").then(function () { location.href = "/"; }); });
    });
  }

  // Watch-page playback lives in player.js.
})();

// Watch page: actions under the video, description box, rail chips.
(function () {
  "use strict";
  function flash(msg) {
    var t = document.getElementById("note-toast");
    if (!t) { t = document.createElement("div"); t.id = "note-toast"; t.className = "note-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(flash._t); flash._t = setTimeout(function () { t.classList.remove("show"); }, 2500);
  }
  var like = document.getElementById("like-btn");
  if (like) like.addEventListener("click", function () {
    fetch("/api/like/" + like.getAttribute("data-id"), { method: "POST" }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        like.classList.toggle("on", d.liked);
        like.setAttribute("aria-pressed", d.liked ? "true" : "false");
        like.querySelector(".yt-like__label").textContent = d.liked ? "Liked" : "Like";
        flash(d.liked ? "Added to Liked videos" : "Removed from Liked videos");
      });
  });
  var share = document.getElementById("share-btn");
  if (share) share.addEventListener("click", function () {
    var u = new URL(location.href);
    var t = Math.floor(window.otubeCurrentTime ? window.otubeCurrentTime() : 0);
    if (t > 0) u.searchParams.set("t", t); else u.searchParams.delete("t");
    var link = u.toString();
    (navigator.clipboard ? navigator.clipboard.writeText(link) : Promise.reject())
      .then(function () { flash("Link copied" + (t > 0 ? " (starts at " + Math.floor(t / 60) + ":" + ("0" + t % 60).slice(-2) + ")" : "")); })
      .catch(function () { window.prompt("Copy this link:", link); });
  });
  var save = document.getElementById("save-btn");
  if (save) save.addEventListener("click", function (e) {
    // reuse the ⋮ menu's "Save to playlist…" (same dialog)
    e.stopPropagation();
    var more = document.querySelector(".watch-more");
    more.click();
    var item = document.querySelector('#ctx-menu [data-act="save"]');
    if (item) item.click();
  });

  // Description box: click to expand, loads technical details once.
  var desc = document.getElementById("yt-desc");
  if (desc) {
    var loaded = false, more = desc.querySelector(".yt-desc__more"), toggle = desc.querySelector(".yt-desc__toggle");
    function setOpen(open) {
      desc.classList.toggle("open", open);
      desc.setAttribute("aria-expanded", open ? "true" : "false");
      more.hidden = !open;
      toggle.textContent = open ? "Show less" : "...more";
      if (open && !loaded) {
        loaded = true;
        more.textContent = "Loading details…";
        fetch("/api/info/" + desc.getAttribute("data-id")).then(function (r) { return r.json(); }).then(function (d) {
          var rows = [
            ["Resolution", d.resolution + (d.fps ? " @ " + d.fps + " fps" : "")],
            ["Video", d.video], ["Audio", d.audio + (d.audio_ch ? " · " + d.audio_ch + " ch" : "")],
            ["Container", d.container], ["Bitrate", d.bitrate ? Math.round(d.bitrate / 1000) + " kb/s" : ""],
            ["Modified", d.modified],
          ].filter(function (r) { return r[1] && r[1].trim(); });
          more.innerHTML = '<div class="yt-desc__grid">' + rows.map(function (r) {
            return '<span>' + r[0] + '</span><span></span>';
          }).join("") + '</div>';
          var cells = more.querySelectorAll(".yt-desc__grid span:nth-child(2n)");
          rows.forEach(function (r, i) { cells[i].textContent = r[1]; });
        }).catch(function () { more.textContent = "Couldn't read file details."; });
      }
    }
    desc.addEventListener("click", function (e) {
      if (e.target.closest(".yt-desc__toggle")) { setOpen(!desc.classList.contains("open")); return; }
      if (!desc.classList.contains("open") && !window.getSelection().toString()) setOpen(true);
    });
    desc.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target === desc) setOpen(!desc.classList.contains("open"));
    });
  }

  // Rail: watched bars + filter chips
  var rail = document.getElementById("rail");
  var prog = {};
  try { prog = JSON.parse(localStorage.getItem("kidtube.progress") || "{}"); } catch (e) {}
  if (rail) rail.querySelectorAll(".up-item").forEach(function (a) {
    var e = prog[a.getAttribute("data-id")];
    if (e && e.d && e.t / e.d > 0.02) {
      a.classList.add("partial");
      a.querySelector(".watched__bar").style.width = Math.min(100, e.t / e.d * 100).toFixed(1) + "%";
      if (e.t / e.d >= 0.97) a.classList.add("seen");
    }
  });
  var chips = document.getElementById("rail-chips");
  if (chips && rail) chips.addEventListener("click", function (e) {
    var c = e.target.closest(".chip"); if (!c) return;
    chips.querySelectorAll(".chip").forEach(function (x) { x.classList.toggle("active", x === c); });
    rail.querySelectorAll(".up-item").forEach(function (a) {
      a.style.display = railMatch(c, a) ? "" : "none";
    });
  });
  function railMatch(c, a) {
    var f = c.getAttribute("data-f"), sub = c.getAttribute("data-sub") || "";
    var s = a.getAttribute("data-sub") || "";
    return f === "all" ||
      (f === "folder" && a.getAttribute("data-same") === "1") ||
      (f === "sub" && a.getAttribute("data-same") === "1" && (s === sub || s.indexOf(sub + "/") === 0)) ||
      (f === "new" && a.getAttribute("data-new") === "1") ||
      (f === "unwatched" && !a.classList.contains("partial"));
  }
  // count on every chip = how many rail videos it would show; hide empty filters
  if (chips && rail) chips.querySelectorAll(".chip").forEach(function (c) {
    var n = 0;
    rail.querySelectorAll(".up-item").forEach(function (a) { if (railMatch(c, a)) n++; });
    var span = document.createElement("span");
    span.className = "chip__n"; span.textContent = n;
    c.appendChild(document.createTextNode(" ")); c.appendChild(span);
    if (!n && c.getAttribute("data-f") !== "all") (c.closest(".chip-wrap") || c).hidden = true;
  });
})();

// ===== App-wide display: brightness + night light (☀ in the top bar) =====
// A fixed overlay above everything (html::after dims, html::before warms with
// multiply blending), so it works on every page element, thumbnails, the mini
// player and fullscreen video, without touching their own styles.
(function () {
  "use strict";
  var KEY = "otube.display", btn = document.getElementById("display-btn");
  var st = { b: 1, n: false, w: 50 };
  try { var saved = JSON.parse(localStorage.getItem(KEY) || "{}"); for (var k in saved) st[k] = saved[k]; } catch (e) {}
  function apply() {
    var r = document.documentElement;
    r.style.setProperty("--app-dim", (1 - st.b).toFixed(2));
    r.style.setProperty("--app-warm", (st.n ? st.w / 100 * 0.45 : 0).toFixed(3));
    try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {}
    if (btn) btn.classList.toggle("on", st.b < 1 || st.n);
  }
  apply();
  if (!btn) return;
  var pop = document.createElement("div");
  pop.className = "display-pop"; pop.hidden = true;
  pop.setAttribute("role", "dialog"); pop.setAttribute("aria-label", "Display settings");
  pop.innerHTML =
    '<div class="dp-head">Display</div>' +
    '<label class="dp-row"><span>Brightness</span><b class="dp-val" data-v="b"></b></label>' +
    '<input type="range" class="dp-range" data-k="b" min="20" max="100" step="1" aria-label="App brightness">' +
    '<div class="dp-row dp-switch-row"><span>Night light</span>' +
    '<button type="button" class="dp-switch" role="switch" aria-label="Night light"><span></span></button></div>' +
    '<label class="dp-row dp-warm"><span>Warmth</span><b class="dp-val" data-v="w"></b></label>' +
    '<input type="range" class="dp-range dp-warm" data-k="w" min="0" max="100" step="1" aria-label="Night light warmth">' +
    '<div class="dp-presets"><button type="button" data-p="normal">Normal</button>' +
    '<button type="button" data-p="dim">Dim</button><button type="button" data-p="night">Night</button></div>' +
    '<p class="dp-note">Dims the whole app. The video player\'s own ☀ adjusts just the video.</p>';
  btn.parentElement.appendChild(pop);
  var bR = pop.querySelector('[data-k="b"]'), wR = pop.querySelector('[data-k="w"]'), sw = pop.querySelector(".dp-switch");
  function sync() {
    bR.value = Math.round(st.b * 100); wR.value = st.w;
    pop.querySelector('[data-v="b"]').textContent = Math.round(st.b * 100) + "%";
    pop.querySelector('[data-v="w"]').textContent = st.w + "%";
    sw.classList.toggle("on", st.n); sw.setAttribute("aria-checked", st.n ? "true" : "false");
    pop.classList.toggle("night-off", !st.n);
    bR.style.setProperty("--fill", ((st.b * 100 - 20) / 80 * 100) + "%");
    wR.style.setProperty("--fill", st.w + "%");
  }
  bR.addEventListener("input", function () { st.b = bR.value / 100; apply(); sync(); });
  wR.addEventListener("input", function () { st.w = +wR.value; st.n = true; apply(); sync(); });
  sw.addEventListener("click", function () { st.n = !st.n; apply(); sync(); });
  pop.querySelector(".dp-presets").addEventListener("click", function (e) {
    var p = e.target.getAttribute("data-p"); if (!p) return;
    if (p === "normal") { st.b = 1; st.n = false; }
    else if (p === "dim") { st.b = 0.6; st.n = false; }
    else { st.b = 0.45; st.n = true; st.w = 70; }
    apply(); sync();
  });
  function setOpen(o) {
    pop.hidden = !o; btn.setAttribute("aria-expanded", o ? "true" : "false");
    if (o) { sync(); bR.focus(); }
  }
  btn.addEventListener("click", function (e) { e.stopPropagation(); setOpen(pop.hidden); });
  document.addEventListener("click", function (e) { if (!pop.hidden && !pop.contains(e.target)) setOpen(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pop.hidden) { setOpen(false); btn.focus(); } });
})();

// ===== Mini player mode: play videos without leaving the page =====
(function () {
  "use strict";
  var toggle = document.getElementById("mini-toggle");
  if (!toggle) return;
  var MODE_KEY = "otube.miniMode", STATE_KEY = "otube.miniState", POS_KEY = "otube.miniPos";
  function get(k, d) { try { return JSON.parse(localStorage.getItem(k)) || d; } catch (e) { return d; } }
  function set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function sget(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } }
  function sset(k, v) { try { if (v == null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }

  var on = !!get(MODE_KEY, false);
  function syncToggle() {
    toggle.classList.toggle("on", on);
    toggle.setAttribute("aria-pressed", on ? "true" : "false");
  }
  syncToggle();
  toggle.addEventListener("click", function () {
    on = !on; set(MODE_KEY, on); syncToggle();
    flash(on ? "Mini player on: videos play here without leaving the page" : "Mini player off: videos open on their own page");
  });

  var ICON = function (d) { return '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="' + d + '"/></svg>'; };
  var dock = document.createElement("div");
  dock.className = "mini"; dock.hidden = true;
  dock.setAttribute("role", "region"); dock.setAttribute("aria-label", "Mini player");
  dock.innerHTML =
    '<div class="mini__bar">' +
    '  <span class="mini__title"></span>' +
    '  <button type="button" class="mini__btn" data-m="size" title="Larger (Esc to shrink)">' + ICON("M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z") + '</button>' +
    '  <button type="button" class="mini__btn" data-m="page" title="Open full page">' + ICON("M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z") + '</button>' +
    '  <button type="button" class="mini__btn" data-m="close" title="Close">' + ICON("M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z") + '</button>' +
    '</div>' +
    '<iframe class="mini__frame" title="Mini player" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>';
  var scrim = document.createElement("div");
  scrim.className = "mini-scrim"; scrim.hidden = true;
  document.body.appendChild(scrim); document.body.appendChild(dock);
  var frame = dock.querySelector("iframe"), titleEl = dock.querySelector(".mini__title");
  var state = null;  // {path, vid, t, title, large}

  function flash(msg) {
    var t = document.getElementById("note-toast");
    if (!t) { t = document.createElement("div"); t.id = "note-toast"; t.className = "note-toast"; document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    clearTimeout(flash._t); flash._t = setTimeout(function () { t.classList.remove("show"); }, 2500);
  }
  function setLarge(l) {
    dock.classList.toggle("mini--large", l); scrim.hidden = !l;
    if (state) { state.large = l; sset(STATE_KEY, state); }
  }
  function open(path, t, large) {
    var u = new URL(path, location.origin);
    u.searchParams.set("embed", "1");
    if (t > 1) u.searchParams.set("t", Math.floor(t)); else u.searchParams.delete("t");
    state = { path: u.pathname + u.search.replace(/[?&]t=\d+/, ""), vid: u.pathname.split("/").pop(), t: t || 0, title: "", large: !!large };
    frame.src = u.pathname + u.search;
    dock.hidden = false;
    setLarge(!!large);
    sset(STATE_KEY, state);
  }
  function close() {
    frame.src = "about:blank"; dock.hidden = true; setLarge(false); state = null; sset(STATE_KEY, null);
  }
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || !e.data || e.data.type !== "otube-mini" || !state) return;
    var d = e.data;
    if (d.vid !== state.vid) {  // autoplay moved to the next video inside the frame
      state.vid = d.vid;
      try { var fu = new URL(frame.contentWindow.location.href); fu.searchParams.delete("t"); state.path = fu.pathname + fu.search; } catch (err) {}
    }
    state.t = d.t; state.title = d.title;
    titleEl.textContent = d.title; titleEl.title = d.title;
    sset(STATE_KEY, state);
  });
  dock.querySelector(".mini__bar").addEventListener("click", function (e) {
    var b = e.target.closest("[data-m]"); if (!b) return;
    var m = b.getAttribute("data-m");
    if (m === "close") close();
    else if (m === "size") setLarge(!dock.classList.contains("mini--large"));
    else if (m === "page" && state) location.href = "/watch/" + state.vid + (state.t > 1 ? "?t=" + Math.floor(state.t) : "");
  });
  scrim.addEventListener("click", function () { setLarge(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && dock.classList.contains("mini--large")) setLarge(false);
  });

  // drag the small player by its bar; position remembered
  var pos = get(POS_KEY, null);
  function place() {
    if (!pos || dock.classList.contains("mini--large")) { dock.style.left = dock.style.top = ""; return; }
    var w = dock.offsetWidth || 400, h = dock.offsetHeight || 260;
    dock.style.left = Math.min(Math.max(8, pos.x), innerWidth - w - 8) + "px";
    dock.style.top = Math.min(Math.max(64, pos.y), innerHeight - h - 8) + "px";
    dock.style.right = dock.style.bottom = "auto";
  }
  var bar = dock.querySelector(".mini__bar"), drag = null;
  bar.addEventListener("pointerdown", function (e) {
    if (e.target.closest("button") || dock.classList.contains("mini--large")) return;
    var r = dock.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    bar.setPointerCapture(e.pointerId); dock.classList.add("dragging");
  });
  bar.addEventListener("pointermove", function (e) {
    if (!drag) return;
    pos = { x: e.clientX - drag.dx, y: e.clientY - drag.dy }; place();
  });
  bar.addEventListener("pointerup", function () { if (drag) { drag = null; dock.classList.remove("dragging"); set(POS_KEY, pos); } });
  window.addEventListener("resize", place);
  new MutationObserver(place).observe(dock, { attributes: true, attributeFilter: ["class", "hidden"] });

  // intercept video links while the mode is on (plain left-clicks only)
  document.addEventListener("click", function (e) {
    if (!on || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest('a[href^="/watch/"]');
    if (!a || a.closest(".mini") || a.closest(".grid.selecting")) return;
    e.preventDefault();
    open(a.getAttribute("href"), 0, dock.classList.contains("mini--large"));
  });

  // keep playing across page changes in this tab (folder chips, sort, search...)
  var prev = sget(STATE_KEY);
  if (prev && prev.path) open(prev.path, prev.t, prev.large);
})();
