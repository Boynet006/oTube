// oTube player: custom controls over the <video> element on the watch page.
// Prev/next, scrubber with buffer + hover time, volume, speed, captions (CC),
// autoplay with an "Up next" countdown, loop, theater, miniplayer (PiP),
// fullscreen, keyboard shortcuts, Media Session (hardware media keys),
// resume-where-you-left-off and watched progress.
(function () {
  "use strict";
  var video = document.getElementById("player");
  var wrap = document.getElementById("player-wrap");
  if (!video || !wrap) return;

  var vid = video.getAttribute("data-id");
  var nextUrl = video.getAttribute("data-next") || "";
  var prevUrl = video.getAttribute("data-prev") || "";
  var nextTitle = video.getAttribute("data-next-title") || "";
  var nextThumb = video.getAttribute("data-next-thumb") || "";
  var title = video.getAttribute("data-title") || document.title;

  // ---------- persisted state ----------
  var PROG_KEY = "kidtube.progress";  // legacy name, keeps old progress
  var PREF_KEY = "otube.player";
  function readJSON(k, d) { try { return JSON.parse(localStorage.getItem(k) || "") || d; } catch (e) { return d; } }
  function writeJSON(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  var prefs = readJSON(PREF_KEY, {});
  var S = window.OTUBE || {};  // app-wide defaults from Settings
  if (prefs.autoplay === undefined) prefs.autoplay = S.autoplay !== false;
  if (prefs.theater === undefined) prefs.theater = !!S.theater;
  if (prefs.ccOn === undefined) prefs.ccOn = !!S.captionsOn;
  function savePrefs() { writeJSON(PREF_KEY, prefs); }

  // ---------- icons ----------
  function svg(d) { return '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path fill="currentColor" d="' + d + '"/></svg>'; }
  var I = {
    play: svg("M8 5.14v13.72a1 1 0 0 0 1.5.86l11-6.86a1 1 0 0 0 0-1.72l-11-6.86A1 1 0 0 0 8 5.14z"),
    pause: svg("M7 5h3.5v14H7zm6.5 0H17v14h-3.5z"),
    replay: svg("M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z"),
    prev: svg("M6 6h2v12H6zm3.5 6 8.5 6V6z"),
    next: svg("M16 6h2v12h-2zM6 18l8.5-6L6 6z"),
    volHigh: svg("M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.47 4.47 0 0 0 2.5-4zM14 3.23v2.06a7 7 0 0 1 0 13.42v2.06A9 9 0 0 0 14 3.23z"),
    volLow: svg("M5 9v6h4l5 5V4L9 9H5zm11.5 3A4.5 4.5 0 0 0 14 8v8a4.47 4.47 0 0 0 2.5-4z"),
    volMute: svg("M16.5 12A4.5 4.5 0 0 0 14 8v2.18l2.45 2.45c.03-.2.05-.41.05-.63zM19 12c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.8 8.8 0 0 0 21 12a9 9 0 0 0-7-8.77v2.06A7 7 0 0 1 19 12zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a9 9 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4 9.91 6.09 12 8.18V4z"),
    cc: svg("M19 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm-8 7H9.5v-.5h-2v3h2V13H11v1a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1zm7 0h-1.5v-.5h-2v3h2V13H18v1a1 1 0 0 1-1 1h-3a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1z"),
    gear: svg("M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.61-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54A.49.49 0 0 0 13.9 2.5h-3.84a.49.49 0 0 0-.49.42l-.36 2.54c-.59.24-1.13.56-1.62.94l-2.39-.96a.5.5 0 0 0-.61.22L2.67 8.98a.49.49 0 0 0 .12.64l2.03 1.58a7.07 7.07 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32c.12.22.37.29.61.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.25.42.49.42h3.84c.24 0 .44-.18.49-.42l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .61-.22l1.92-3.32a.49.49 0 0 0-.12-.64l-2.03-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z"),
    mini: svg("M19 11h-8v6h8v-6zm4 8V4.98C23 3.88 22.1 3 21 3H3a2 2 0 0 0-2 1.98V19c0 1.1.9 2 2 2h18a2 2 0 0 0 2-2zm-2 .02H3V4.97h18v14.05z"),
    theater: svg("M19 6H5a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2zm0 10H5V8h14v8z"),
    theaterOff: svg("M19 7H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zm0 8H5V9h14v6z"),
    full: svg("M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"),
    fullExit: svg("M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"),
    back10: svg("M11.99 5V1l-5 5 5 5V7a6 6 0 1 1-6 6h-2a8 8 0 1 0 8-8zm-1.1 11h-.85v-3.26l-1.01.31v-.69l1.77-.63h.09V16zm4.28-1.76c0 .32-.03.6-.1.82s-.17.42-.29.57-.28.26-.45.33-.37.1-.59.1-.41-.03-.59-.1-.33-.18-.46-.33-.23-.34-.3-.57-.11-.5-.11-.82v-.74c0-.32.03-.6.1-.82s.17-.42.29-.57.28-.26.45-.33.37-.1.59-.1.41.03.59.1.33.18.46.33.23.34.3.57.11.5.11.82v.74zm-.85-.86c0-.19-.01-.35-.04-.48s-.07-.23-.12-.31-.11-.14-.19-.17-.16-.05-.25-.05-.18.02-.25.05-.14.09-.19.17-.09.18-.12.31-.04.29-.04.48v.97c0 .19.01.35.04.48s.07.24.12.32.11.14.19.17.16.05.25.05.18-.02.25-.05.14-.09.19-.17.09-.19.11-.32.04-.29.04-.48v-.97z"),
    fwd10: svg("M18 13a6 6 0 1 1-6-6v4l5-5-5-5v4a8 8 0 1 0 8 8h-2zm-7.1 3h-.85v-3.26l-1.01.31v-.69l1.77-.63h.09V16zm4.28-1.76c0 .32-.03.6-.1.82s-.17.42-.29.57-.28.26-.45.33-.37.1-.59.1-.41-.03-.59-.1-.33-.18-.46-.33-.23-.34-.3-.57-.11-.5-.11-.82v-.74c0-.32.03-.6.1-.82s.17-.42.29-.57.28-.26.45-.33.37-.1.59-.1.41.03.59.1.33.18.46.33.23.34.3.57.11.5.11.82v.74zm-.85-.86c0-.19-.01-.35-.04-.48s-.07-.23-.12-.31-.11-.14-.19-.17-.16-.05-.25-.05-.18.02-.25.05-.14.09-.19.17-.09.18-.12.31-.04.29-.04.48v.97c0 .19.01.35.04.48s.07.24.12.32.11.14.19.17.16.05.25.05.18-.02.25-.05.14-.09.19-.17.09-.19.11-.32.04-.29.04-.48v-.97z"),
    sun: svg("M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM2 13h2a1 1 0 0 0 0-2H2a1 1 0 0 0 0 2zm18 0h2a1 1 0 0 0 0-2h-2a1 1 0 0 0 0 2zM11 2v2a1 1 0 0 0 2 0V2a1 1 0 0 0-2 0zm0 18v2a1 1 0 0 0 2 0v-2a1 1 0 0 0-2 0zM5.99 4.58a1 1 0 0 0-1.41 1.41l1.06 1.06a1 1 0 0 0 1.41-1.41L5.99 4.58zm12.37 12.37a1 1 0 0 0-1.41 1.41l1.06 1.06a1 1 0 0 0 1.41-1.41l-1.06-1.06zm1.06-10.96a1 1 0 0 0-1.41-1.41l-1.06 1.06a1 1 0 0 0 1.41 1.41l1.06-1.06zM7.05 18.36a1 1 0 0 0-1.41-1.41l-1.06 1.06a1 1 0 0 0 1.41 1.41l1.06-1.06z"),
    moon: svg("M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.39 5.39 0 0 1-4.4 2.26 5.4 5.4 0 0 1-3.14-9.8c-.44-.06-.9-.1-1.36-.1z"),
    check: svg("M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"),
    chev: svg("M10 6 8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"),
    chevL: svg("M15.41 7.41 14 6l-6 6 6 6 1.41-1.41L10.83 12z"),
  };

  // ---------- build controls ----------
  video.removeAttribute("controls");
  wrap.classList.add("p");
  wrap.insertAdjacentHTML("beforeend",
    '<div class="p-spinner" aria-hidden="true"></div>' +
    '<div class="p-flash" aria-hidden="true"></div>' +
    '<div class="p-toast" aria-live="polite"></div>' +
    '<div class="p-upnext" hidden>' +
    '  <div class="p-upnext__label">Up next in <b class="p-upnext__n">5</b></div>' +
    '  <div class="p-upnext__card"><img alt=""><div class="p-upnext__title"></div></div>' +
    '  <div class="p-upnext__btns"><button type="button" class="p-upnext__cancel">Cancel</button>' +
    '  <button type="button" class="p-upnext__play">Play now</button></div>' +
    '</div>' +
    '<div class="p-menu" hidden role="menu"></div>' +
    '<div class="p-controls">' +
    '  <div class="p-scrub" role="slider" tabindex="0" aria-label="Seek">' +
    '    <div class="p-scrub__rail"><div class="p-scrub__buf"></div><div class="p-scrub__hover"></div><div class="p-scrub__fill"></div></div>' +
    '    <div class="p-scrub__knob"></div><div class="p-scrub__tip"></div>' +
    '  </div>' +
    '  <div class="p-bar">' +
    '    <div class="p-left">' +
    '     <div class="p-pill">' +
    '      <button type="button" class="p-btn" data-a="prev" title="Previous (Shift+P)" aria-label="Previous">' + I.prev + '</button>' +
    '      <button type="button" class="p-btn" data-a="play" title="Play (k)" aria-label="Play">' + I.play + '</button>' +
    '      <button type="button" class="p-btn" data-a="next" title="Next (Shift+N)" aria-label="Next">' + I.next + '</button>' +
    '     </div><div class="p-pill">' +
    '      <div class="p-vol">' +
    '        <button type="button" class="p-btn" data-a="mute" title="Mute (m)" aria-label="Mute">' + I.volHigh + '</button>' +
    '        <input type="range" class="p-vol__range" min="0" max="1" step="0.02" aria-label="Volume">' +
    '      </div>' +
    '      <div class="p-vol p-bright">' +
    '        <button type="button" class="p-btn" data-a="bright" title="Brightness (b / Shift+B)" aria-label="Brightness">' + I.sun + '</button>' +
    '        <input type="range" class="p-vol__range p-bright__range" min="0" max="1" step="0.05" aria-label="Brightness">' +
    '      </div>' +
    '     </div><div class="p-pill">' +
    '      <button type="button" class="p-time" data-a="time" title="Toggle remaining time"><span class="p-cur">0:00</span> / <span class="p-dur">0:00</span></button>' +
    '     </div>' +
    '    </div>' +
    '    <div class="p-right">' +
    '     <div class="p-pill">' +
    '      <button type="button" class="p-switch" data-a="autoplay" title="Autoplay" aria-label="Autoplay" role="switch"><span class="p-switch__track"><span class="p-switch__knob"></span></span></button>' +
    '      <button type="button" class="p-btn" data-a="cc" title="Subtitles/closed captions (c)" aria-label="Subtitles">' + I.cc + '</button>' +
    '      <button type="button" class="p-btn" data-a="settings" title="Settings" aria-label="Settings" aria-haspopup="menu">' + I.gear + '</button>' +
    '      <button type="button" class="p-btn" data-a="mini" title="Miniplayer (i)" aria-label="Miniplayer">' + I.mini + '</button>' +
    '      <button type="button" class="p-btn" data-a="theater" title="Theater mode (t)" aria-label="Theater mode">' + I.theater + '</button>' +
    '      <button type="button" class="p-btn" data-a="full" title="Full screen (f)" aria-label="Full screen">' + I.full + '</button>' +
    '     </div>' +
    '    </div>' +
    '  </div>' +
    '</div>');

  function $(sel) { return wrap.querySelector(sel); }
  function btn(a) { return wrap.querySelector('[data-a="' + a + '"]'); }
  var scrub = $(".p-scrub"), fill = $(".p-scrub__fill"), buf = $(".p-scrub__buf"),
      hoverBar = $(".p-scrub__hover"), knob = $(".p-scrub__knob"), tip = $(".p-scrub__tip");
  var volRange = $(".p-vol__range"), curEl = $(".p-cur"), durEl = $(".p-dur");
  var menu = $(".p-menu"), flash = $(".p-flash"), toastEl = $(".p-toast");
  var upnext = $(".p-upnext");

  if (!nextUrl) btn("next").disabled = true;

  // ---------- helpers ----------
  function fmt(t) {
    if (!isFinite(t) || t < 0) t = 0;
    t = Math.floor(t);
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (s < 10 ? "0" : "") + s;
  }
  function dur() { return isFinite(video.duration) ? video.duration : 0; }
  var toastTimer;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.classList.remove("show"); }, 900);
  }
  function flashIcon(icon) {
    flash.innerHTML = icon;
    flash.classList.remove("go"); void flash.offsetWidth; flash.classList.add("go");
  }

  // ---------- play / pause ----------
  function togglePlay() {
    if (video.paused || video.ended) { video.play().catch(function () {}); }
    else video.pause();
  }
  function syncPlay() {
    var b = btn("play");
    var icon = video.ended ? I.replay : (video.paused ? I.play : I.pause);
    b.innerHTML = icon;
    b.setAttribute("aria-label", video.paused ? "Play" : "Pause");
    b.title = (video.paused ? "Play" : "Pause") + " (k)";
    wrap.classList.toggle("paused", video.paused);
  }
  ["play", "pause", "ended"].forEach(function (e) { video.addEventListener(e, syncPlay); });
  video.addEventListener("play", function () { hideUpnext(); poke(); });
  video.addEventListener("pause", function () { showControls(); });

  // Click toggles play; double-click toggles fullscreen (click is delayed so a
  // double-click doesn't also pause/play).
  var clickTimer = null;
  var suppressClick = false;  // set after the mini player was dragged by the video
  // Touch: the first tap only reveals the controls; the next one plays/pauses (YouTube app)
  var lastPointer = "mouse";
  wrap.addEventListener("pointerdown", function (e) { lastPointer = e.pointerType; }, true);
  video.addEventListener("click", function () {
    if (suppressClick) { suppressClick = false; return; }
    if (lastPointer === "touch" && !video.paused && !wrap.classList.contains("show-ui")) { poke(); return; }
    if (clickTimer) return;
    clickTimer = setTimeout(function () {
      clickTimer = null;
      togglePlay();
      flashIcon(video.paused ? I.pause : I.play);
    }, 220);
  });
  video.addEventListener("dblclick", function () {
    clearTimeout(clickTimer); clickTimer = null;
    toggleFull();
  });

  // ---------- prev / next ----------
  function goNext() { if (nextUrl) location.href = nextUrl; }
  function goPrev() {
    // YouTube behaviour: past 3s, "previous" restarts the current video.
    if (video.currentTime > 3 || (!prevUrl && !cameFromWatch())) { video.currentTime = 0; return; }
    if (prevUrl) location.href = prevUrl; else history.back();
  }
  function cameFromWatch() {
    try { return document.referrer && new URL(document.referrer).origin === location.origin &&
                 new URL(document.referrer).pathname.indexOf("/watch/") === 0; }
    catch (e) { return false; }
  }

  // ---------- time / scrubber ----------
  var showRemaining = false;
  function syncTime() {
    var d = dur(), t = video.currentTime;
    curEl.textContent = showRemaining && d ? "-" + fmt(d - t) : fmt(t);
    durEl.textContent = d ? fmt(d) : "--:--";
    var pct = d ? (t / d) * 100 : 0;
    if (!dragging) { fill.style.width = pct + "%"; knob.style.left = pct + "%"; }
    scrub.setAttribute("aria-valuenow", Math.round(t));
    scrub.setAttribute("aria-valuemax", Math.round(d));
    scrub.setAttribute("aria-valuetext", fmt(t) + " of " + fmt(d));
  }
  function syncBuffer() {
    var d = dur(), b = video.buffered, end = 0;
    for (var i = 0; i < b.length; i++) {
      if (b.start(i) <= video.currentTime + 0.5) end = Math.max(end, b.end(i));
    }
    buf.style.width = d ? Math.min(100, (end / d) * 100) + "%" : "0";
  }
  video.addEventListener("timeupdate", syncTime);
  video.addEventListener("durationchange", syncTime);
  video.addEventListener("loadedmetadata", syncTime);
  video.addEventListener("progress", syncBuffer);
  video.addEventListener("timeupdate", syncBuffer);
  btn("time").addEventListener("click", function () { showRemaining = !showRemaining; syncTime(); });

  var dragging = false, wasPaused = false;
  function ratioAt(e) {
    var r = scrub.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }
  function previewAt(ratio) {
    var pct = ratio * 100;
    hoverBar.style.width = pct + "%";
    tip.textContent = fmt(ratio * dur());
    var r = scrub.getBoundingClientRect(), tw = tip.offsetWidth / 2;
    tip.style.left = Math.min(r.width - tw, Math.max(tw, ratio * r.width)) + "px";
  }
  scrub.addEventListener("pointermove", function (e) {
    var ratio = ratioAt(e);
    previewAt(ratio);
    if (dragging) { fill.style.width = knob.style.left = ratio * 100 + "%"; video.currentTime = ratio * dur(); }
  });
  scrub.addEventListener("pointerleave", function () { if (!dragging) hoverBar.style.width = "0"; });
  scrub.addEventListener("pointerdown", function (e) {
    if (!dur()) return;
    dragging = true; wasPaused = video.paused;
    scrub.setPointerCapture(e.pointerId);
    scrub.classList.add("dragging");
    video.pause();
    var ratio = ratioAt(e);
    fill.style.width = knob.style.left = ratio * 100 + "%";
    video.currentTime = ratio * dur();
  });
  function endDrag() {
    if (!dragging) return;
    dragging = false;
    scrub.classList.remove("dragging");
    hoverBar.style.width = "0";
    if (!wasPaused) video.play().catch(function () {});
  }
  scrub.addEventListener("pointerup", endDrag);
  scrub.addEventListener("pointercancel", endDrag);
  scrub.addEventListener("keydown", function (e) {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.stopPropagation(); e.preventDefault(); seekBy(e.key === "ArrowLeft" ? -5 : 5); }
  });

  function seekBy(s) {
    video.currentTime = Math.min(dur() || Infinity, Math.max(0, video.currentTime + s));
    flashIcon(s < 0 ? I.back10 : I.fwd10);
    syncTime(); poke();
  }

  // ---------- volume ----------
  function syncVol() {
    var v = video.muted ? 0 : video.volume;
    volRange.value = v;
    volRange.style.setProperty("--fill", v * 100 + "%");
    var b = btn("mute");
    b.innerHTML = v === 0 ? I.volMute : v < 0.5 ? I.volLow : I.volHigh;
    b.setAttribute("aria-label", video.muted ? "Unmute" : "Mute");
    b.title = (video.muted ? "Unmute" : "Mute") + " (m)";
  }
  video.volume = typeof prefs.vol === "number" ? prefs.vol : (S.volume != null ? S.volume / 100 : 1);
  if (S.speed && S.speed !== 1) video.playbackRate = video.defaultPlaybackRate = S.speed;
  video.addEventListener("volumechange", function () {
    syncVol();
    prefs.vol = video.volume; savePrefs();
  });
  volRange.addEventListener("input", function () {
    video.volume = +volRange.value;
    video.muted = video.volume === 0;
  });
  function toggleMute() {
    if (video.muted || video.volume === 0) { video.muted = false; if (video.volume === 0) video.volume = 0.5; }
    else video.muted = true;
  }
  function volBy(d) {
    video.muted = false;
    video.volume = Math.min(1, Math.max(0, Math.round((video.volume + d) * 100) / 100));
    toast(Math.round(video.volume * 100) + "%");
  }
  syncVol();

  // ---------- brightness (dims the picture itself, like a screen dimmer) ----------
  // CSS brightness() on the <video>: no extra layer over the video, so clicks,
  // captions and fullscreen all keep working, and it's GPU-cheap.
  var brightRange = $(".p-bright__range");
  var lastDim = typeof prefs.lastDim === "number" ? prefs.lastDim : 0.6;
  function setBright(v, quiet) {
    v = Math.min(1, Math.max(0, Math.round(v * 100) / 100));
    prefs.bright = v;
    if (v < 1) { prefs.lastDim = lastDim = v; }
    savePrefs();
    video.style.filter = v < 1 ? "brightness(" + v + ")" : "";
    brightRange.value = v;
    brightRange.style.setProperty("--fill", v * 100 + "%");
    if (!quiet) toast("Brightness " + Math.round(v * 100) + "%");
  }
  brightRange.addEventListener("input", function () { setBright(+brightRange.value, true); });
  setBright(typeof prefs.bright === "number" ? prefs.bright : 1, true);

  // ---------- speed ----------
  var SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  function setRate(r) {
    video.playbackRate = r;
    toast(r + "x");
  }
  function rateStep(dir) {
    var i = SPEEDS.indexOf(video.playbackRate);
    if (i === -1) i = 3;
    i = Math.min(SPEEDS.length - 1, Math.max(0, i + dir));
    setRate(SPEEDS[i]);
  }

  // ---------- captions ----------
  var subs = [], ccIndex = -1;  // -1 = off
  function applyCC() {
    for (var i = 0; i < video.textTracks.length; i++) {
      video.textTracks[i].mode = i === ccIndex ? "showing" : "disabled";
    }
    var b = btn("cc");
    b.classList.toggle("on", ccIndex >= 0);
    b.setAttribute("aria-pressed", ccIndex >= 0 ? "true" : "false");
  }
  function setCC(i, quiet) {
    ccIndex = i;
    applyCC();
    if (i >= 0) { prefs.cc = subs[i].lang || subs[i].label; prefs.ccOn = true; }
    else prefs.ccOn = false;
    savePrefs();
    if (!quiet) toast(i >= 0 ? "Subtitles: " + subs[i].label : "Subtitles off");
  }
  function toggleCC() {
    if (!subs.length) { openMenu(); renderCCView(); return; }  // offer auto-generate
    if (ccIndex >= 0) setCC(-1);
    else {
      var pref = subs.findIndex(function (s) { return (s.lang || s.label) === prefs.cc; });
      setCC(pref >= 0 ? pref : 0);
    }
  }
  // Track list + server-side caption jobs (speech-to-text and translation).
  var capInfo = { auto: { state: "none" }, translate: [], source_lang: null };
  function busy(st) { return st && (st.state === "queued" || st.state === "running"); }
  function jobKeys(info) {  // job name -> track key it produces
    var m = { auto: info.auto };
    (info.translate || []).forEach(function (t) { m["tr-" + t.lang] = t; });
    return m;
  }
  function loadSubs(selectKey) {
    return fetch("/subs/" + vid).then(function (r) { return r.json(); }).then(function (d) {
      [].slice.call(video.querySelectorAll("track")).forEach(function (t) { t.remove(); });
      subs = d.tracks || [];
      capInfo = d;
      subs.forEach(function (s) {
        var t = document.createElement("track");
        t.kind = "subtitles"; t.label = s.label; t.src = s.src;
        if (s.lang) t.srclang = s.lang;
        video.appendChild(t);
      });
      var want = selectKey ? subs.findIndex(function (s) { return s.key === selectKey; }) : -1;
      var pref = subs.findIndex(function (s) { return (s.lang || s.label) === prefs.cc; });
      if (want >= 0) setCC(want, true);
      else if (prefs.ccOn && subs.length) setCC(pref >= 0 ? pref : 0, true);
      else { ccIndex = -1; applyCC(); }
      if (menu.getAttribute("data-view") === "cc" && !menu.hidden) renderCCView();
      var jobs = jobKeys(d);
      if (Object.keys(jobs).some(function (k) { return busy(jobs[k]); })) pollJobs();
    }).catch(function () {});
  }
  var pollTimer = null;
  function pollJobs() {
    clearTimeout(pollTimer);
    var before = jobKeys(capInfo);
    pollTimer = setTimeout(function () {
      fetch("/subs/" + vid).then(function (r) { return r.json(); }).then(function (d) {
        var after = jobKeys(d), finished = null, failed = false, running = false;
        Object.keys(after).forEach(function (k) {
          if (busy(before[k]) && after[k].state === "done") finished = k;
          if (busy(before[k]) && after[k].state === "error") failed = true;
          if (busy(after[k])) running = true;
        });
        capInfo = d;
        if (finished) { loadSubs(finished); toast(finished === "auto" ? "Captions ready" : "Translation ready"); }
        else if (menu.getAttribute("data-view") === "cc" && !menu.hidden) renderCCView();
        if (failed) toast("Caption job failed");
        if (running && !finished) pollJobs();
      }).catch(pollJobs);
    }, 2000);
  }
  function startJob(url, msg) {
    fetch(url, { method: "POST" }).then(function (r) { return r.json(); }).then(function (st) {
      if (!st.ok) { toast(st.error || "Failed"); return; }
      loadSubs();  // picks up the queued state and starts polling
    });
    toast(msg);
  }
  function generateCaptions() { capInfo.auto = { state: "queued", progress: 0 }; startJob("/subs/" + vid + "/generate", "Generating captions…"); }
  function translateTo(lang, name) {
    (capInfo.translate || []).forEach(function (t) { if (t.lang === lang) { t.state = "queued"; t.progress = 0; } });
    startJob("/subs/" + vid + "/translate/" + lang, "Translating to " + name + "…");
  }
  loadSubs();

  // ---------- autoplay / loop ----------
  function syncAutoplay() {
    var b = btn("autoplay");
    b.classList.toggle("on", !!prefs.autoplay);
    b.setAttribute("aria-checked", prefs.autoplay ? "true" : "false");
    b.title = "Autoplay is " + (prefs.autoplay ? "on" : "off");
  }
  function toggleAutoplay() { prefs.autoplay = !prefs.autoplay; savePrefs(); syncAutoplay(); toast("Autoplay " + (prefs.autoplay ? "on" : "off")); }
  syncAutoplay();

  // ---------- theater ----------
  var slot = document.getElementById("theater-slot");
  var home = wrap.parentNode, homeNext = wrap.nextSibling;
  function setTheater(on) {
    prefs.theater = !!on; savePrefs();
    document.body.classList.toggle("theater", !!on);
    if (slot) {
      if (on) slot.appendChild(wrap);
      else home.insertBefore(wrap, homeNext);
    }
    var b = btn("theater");
    b.innerHTML = on ? I.theaterOff : I.theater;
    b.title = (on ? "Default view" : "Theater mode") + " (t)";
  }
  setTheater(!!prefs.theater);

  // ---------- fullscreen / miniplayer ----------
  function toggleFull() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (wrap.requestFullscreen) wrap.requestFullscreen().catch(function () {});
  }
  document.addEventListener("fullscreenchange", function () {
    var fs = document.fullscreenElement === wrap;
    wrap.classList.toggle("fs", fs);
    var b = btn("full");
    b.innerHTML = fs ? I.fullExit : I.full;
    b.title = (fs ? "Exit full screen" : "Full screen") + " (f)";
  });
  function toggleMini() {
    if (document.pictureInPictureElement) document.exitPictureInPicture();
    else if (video.requestPictureInPicture) video.requestPictureInPicture().catch(function () {});
  }
  if (!document.pictureInPictureEnabled) btn("mini").hidden = true;

  // ---------- settings menu ----------
  function closeMenu() { menu.hidden = true; btn("settings").classList.remove("on"); }
  function row(label, value, action, extra) {
    return '<button type="button" class="p-menu__row" data-m="' + action + '"' + (extra || "") + '>' +
      '<span class="p-menu__label">' + label + '</span>' +
      '<span class="p-menu__value">' + value + '</span></button>';
  }
  function renderCCView() {
    renderList("Subtitles/CC",
      [{ v: -1, label: "Off" }].concat(subs.map(function (s, i) { return { v: i, label: s.label }; })), ccIndex, "cc");
    menu.setAttribute("data-view", "cc");
    var st = capInfo.auto.state, html;
    function pct(j) { return j.state === "queued" ? "Waiting" : Math.round((j.progress || 0) * 100) + "%"; }
    if (busy(capInfo.auto)) {
      html = row("Generating captions…", pct(capInfo.auto), "noop", " disabled");
    } else if (st === "done") {
      html = row("Regenerate captions", "", "gen");
    } else {
      html = row("✨ Auto-generate captions", st === "error" ? "Retry" : "Speech-to-text", "gen");
    }
    (capInfo.translate || []).forEach(function (t) {
      if (t.lang === capInfo.source_lang || t.state === "done") return;
      html += busy(t)
        ? row("Translating to " + t.name + "…", pct(t), "noop", " disabled")
        : row("🌐 Translate to " + t.name, t.state === "error" ? "Retry" : "", "tr",
              ' data-v="' + t.lang + '" data-name="' + t.name + '"');
    });
    menu.insertAdjacentHTML("beforeend", '<div class="p-menu__sep"></div>' + html);
  }
  function renderMain() {
    menu.setAttribute("data-view", "main");
    var ccLabel = !subs.length ? "None" : ccIndex >= 0 ? subs[ccIndex].label : "Off";
    var rate = video.playbackRate === 1 ? "Normal" : video.playbackRate + "x";
    menu.innerHTML =
      row("Autoplay", '<span class="p-mini-switch' + (prefs.autoplay ? " on" : "") + '"></span>', "autoplay") +
      row("Loop", '<span class="p-mini-switch' + (video.loop ? " on" : "") + '"></span>', "loop") +
      row("Subtitles/CC", ccLabel + " " + I.chev, "cc-view") +
      row("Playback speed", rate + " " + I.chev, "speed-view") +
      row("Brightness", Math.round((typeof prefs.bright === "number" ? prefs.bright : 1) * 100) + "% " + I.chev, "bright-view") +
      row("Keyboard shortcuts", I.chev, "keys");
  }
  function renderList(titleText, items, current, action) {
    menu.setAttribute("data-view", "list");
    menu.innerHTML = '<button type="button" class="p-menu__head" data-m="back">' + I.chevL + titleText + '</button>' +
      items.map(function (it) {
        return '<button type="button" class="p-menu__row opt" data-m="' + action + '" data-v="' + it.v + '">' +
          '<span class="p-menu__check">' + (String(it.v) === String(current) ? I.check : "") + '</span>' +
          '<span class="p-menu__label">' + it.label + '</span></button>';
      }).join("");
  }
  function openMenu() { renderMain(); menu.hidden = false; btn("settings").classList.add("on"); showControls(); }
  menu.addEventListener("click", function (e) {
    var b = e.target.closest("[data-m]"); if (!b) return;
    e.stopPropagation();
    var m = b.getAttribute("data-m");
    if (m === "back") renderMain();
    else if (m === "autoplay") { toggleAutoplay(); renderMain(); }
    else if (m === "loop") { video.loop = !video.loop; renderMain(); }
    else if (m === "speed-view") renderList("Playback speed",
      SPEEDS.map(function (s) { return { v: s, label: s === 1 ? "Normal" : s + "" }; }), video.playbackRate, "speed");
    else if (m === "cc-view") renderCCView();
    else if (m === "gen") { generateCaptions(); renderCCView(); }
    else if (m === "tr") { translateTo(b.getAttribute("data-v"), b.getAttribute("data-name")); renderCCView(); }
    else if (m === "bright-view") renderList("Brightness",
      [1, 0.8, 0.6, 0.4, 0.2, 0].map(function (v) { return { v: v, label: v === 1 ? "Normal" : Math.round(v * 100) + "%" }; }),
      typeof prefs.bright === "number" ? prefs.bright : 1, "bright");
    else if (m === "bright") { setBright(+b.getAttribute("data-v")); closeMenu(); }
    else if (m === "speed") { setRate(+b.getAttribute("data-v")); closeMenu(); }
    else if (m === "cc") { setCC(+b.getAttribute("data-v")); closeMenu(); }
    else if (m === "keys") { closeMenu(); showKeys(); }
  });
  document.addEventListener("click", function (e) {
    if (!menu.hidden && !menu.contains(e.target) && !btn("settings").contains(e.target)) closeMenu();
  });

  // ---------- keyboard shortcuts help ----------
  var KEYS = [
    ["Space / k", "Play / pause"], ["j / l", "Back / forward 10s"], ["← / →", "Back / forward 5s"],
    ["↑ / ↓", "Volume up / down"], ["m", "Mute"], ["Shift+N / Shift+P", "Next / previous video"],
    ["< / >", "Slower / faster"], ["c", "Subtitles on / off"], ["b / Shift+B", "Darker / brighter"], ["t", "Theater mode"],
    ["f", "Full screen"], ["i", "Miniplayer"], ["0–9", "Jump to 0%–90%"],
    ["Home / End", "Start / end"], [", / .", "Previous / next frame (paused)"], ["?", "Show shortcuts"],
  ];
  var keysModal = null;
  function showKeys() {
    if (!keysModal) {
      keysModal = document.createElement("div");
      keysModal.className = "modal-overlay";
      keysModal.innerHTML = '<div class="modal modal--sm"><div class="modal__head"><span>Keyboard shortcuts</span>' +
        '<button class="modal__close" data-close aria-label="Close">✕</button></div><div class="keys-list">' +
        KEYS.map(function (k) { return '<div class="keys-row"><kbd>' + k[0] + '</kbd><span>' + k[1] + '</span></div>'; }).join("") +
        '</div></div>';
      document.body.appendChild(keysModal);
      keysModal.addEventListener("click", function (e) {
        if (e.target === keysModal || e.target.closest("[data-close]")) keysModal.hidden = true;
      });
    }
    keysModal.hidden = false;
  }

  // ---------- control bar buttons ----------
  wrap.querySelector(".p-bar").addEventListener("click", function (e) {
    var b = e.target.closest("[data-a]"); if (!b) return;
    var a = b.getAttribute("data-a");
    if (a === "play") togglePlay();
    else if (a === "prev") goPrev();
    else if (a === "next") goNext();
    else if (a === "mute") toggleMute();
    else if (a === "bright") setBright((typeof prefs.bright === "number" ? prefs.bright : 1) < 1 ? 1 : lastDim);
    else if (a === "cc") toggleCC();
    else if (a === "autoplay") toggleAutoplay();
    else if (a === "settings") { if (menu.hidden) openMenu(); else closeMenu(); }
    else if (a === "mini") toggleMini();
    else if (a === "theater") setTheater(!prefs.theater);
    else if (a === "full") toggleFull();
  });

  // ---------- auto-hide controls ----------
  var hideTimer = null;
  function showControls() { wrap.classList.add("show-ui"); clearTimeout(hideTimer); }
  function poke() {
    showControls();
    hideTimer = setTimeout(function () {
      if (!video.paused && menu.hidden && !dragging) wrap.classList.remove("show-ui");
    }, 2600);
  }
  wrap.addEventListener("mousemove", poke);
  wrap.addEventListener("touchstart", poke, { passive: true });
  wrap.addEventListener("mouseleave", function () {
    if (!video.paused && menu.hidden) { clearTimeout(hideTimer); wrap.classList.remove("show-ui"); }
  });
  showControls();

  // ---------- buffering spinner ----------
  var spinTimer = null;
  video.addEventListener("waiting", function () {
    clearTimeout(spinTimer);
    spinTimer = setTimeout(function () { wrap.classList.add("buffering"); }, 250);
  });
  ["playing", "canplay", "pause", "seeked"].forEach(function (ev) {
    video.addEventListener(ev, function () { clearTimeout(spinTimer); wrap.classList.remove("buffering"); });
  });

  // ---------- resume + progress ----------
  var prog = readJSON(PROG_KEY, {});
  video.addEventListener("loadedmetadata", function () {
    var entry = prog[vid];
    var tParam = parseFloat(new URLSearchParams(location.search).get("t"));  // shared link time
    if (tParam > 0 && dur()) {
      try { video.currentTime = Math.min(tParam, dur() - 1); } catch (e) {}
    } else if (S.resume !== false && entry && entry.t > 5 && dur() && entry.t < dur() - 10) {
      try { video.currentTime = entry.t; } catch (e) {}
      toast("Resumed at " + fmt(entry.t));
    }
    // Autoplay with sound; if the browser blocks it, stay paused (never force-mute).
    var p = video.play();
    if (p && p.catch) p.catch(function () { showControls(); });
  });
  var lastStore = 0;
  function store(done) {
    if (!dur()) return;
    var p = readJSON(PROG_KEY, {});
    p[vid] = { t: done ? dur() : video.currentTime, d: dur(), ts: Date.now() };
    writeJSON(PROG_KEY, p);
  }
  video.addEventListener("timeupdate", function () {
    if (Math.abs(video.currentTime - lastStore) > 5) { lastStore = video.currentTime; store(); }
  });
  video.addEventListener("pause", function () { store(); });
  window.addEventListener("pagehide", function () { store(); });

  // ---------- ended: autoplay countdown ----------
  var countdown = null;
  function hideUpnext() { clearInterval(countdown); countdown = null; upnext.hidden = true; }
  video.addEventListener("ended", function () {
    store(true);
    if (video.loop || !nextUrl || !prefs.autoplay) { showControls(); return; }
    upnext.querySelector("img").src = nextThumb;
    upnext.querySelector(".p-upnext__title").textContent = nextTitle;
    if (S.upnextSeconds === 0) { goNext(); return; }
    var n = S.upnextSeconds || 5, nEl = upnext.querySelector(".p-upnext__n");
    nEl.textContent = n;
    upnext.hidden = false;
    countdown = setInterval(function () {
      n -= 1; nEl.textContent = n;
      if (n <= 0) { hideUpnext(); goNext(); }
    }, 1000);
  });
  upnext.querySelector(".p-upnext__cancel").addEventListener("click", hideUpnext);
  upnext.querySelector(".p-upnext__play").addEventListener("click", function () { hideUpnext(); goNext(); });
  upnext.querySelector(".p-upnext__card").addEventListener("click", function () { hideUpnext(); goNext(); });

  // ---------- keyboard ----------
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key, handled = true;
    if (k === " " || k === "k") {
      if (k === " " && t && t.tagName === "BUTTON") return;  // let buttons handle Space
      togglePlay(); flashIcon(video.paused ? I.pause : I.play);
    }
    else if (k === "j") seekBy(-10);
    else if (k === "l") seekBy(10);
    else if (k === "ArrowLeft") seekBy(-(S.seekStep || 5));
    else if (k === "ArrowRight") seekBy(S.seekStep || 5);
    else if (k === "ArrowUp") volBy(0.05);
    else if (k === "ArrowDown") volBy(-0.05);
    else if (k === "m") { toggleMute(); toast(video.muted ? "Muted" : "Unmuted"); }
    else if (k === "f") toggleFull();
    else if (k === "t") setTheater(!prefs.theater);
    else if (k === "i") toggleMini();
    else if (k === "c") toggleCC();
    else if (k === "b") setBright((typeof prefs.bright === "number" ? prefs.bright : 1) - 0.1);
    else if (k === "B") setBright((typeof prefs.bright === "number" ? prefs.bright : 1) + 0.1);
    else if (k === "N" && e.shiftKey) goNext();
    else if (k === "P" && e.shiftKey) goPrev();
    else if (k === ">") rateStep(1);
    else if (k === "<") rateStep(-1);
    else if (k === "Home") { video.currentTime = 0; }
    else if (k === "End") { if (dur()) video.currentTime = dur() - 0.1; }
    else if (k === "," && video.paused) video.currentTime = Math.max(0, video.currentTime - 1 / 30);
    else if (k === "." && video.paused) video.currentTime = Math.min(dur(), video.currentTime + 1 / 30);
    else if (k === "?") showKeys();
    else if (k === "Escape") { closeMenu(); if (keysModal) keysModal.hidden = true; }
    else if (k >= "0" && k <= "9") { if (dur()) video.currentTime = dur() * (parseInt(k, 10) / 10); }
    else handled = false;
    if (handled) { e.preventDefault(); poke(); }
  });

  // ---------- Media Session (OS media keys / lock screen) ----------
  if ("mediaSession" in navigator) {
    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: title, artist: "oTube",
        artwork: [{ src: "/thumb/" + vid, sizes: "480x270", type: "image/jpeg" }],
      });
      var ms = navigator.mediaSession;
      ms.setActionHandler("play", function () { video.play(); });
      ms.setActionHandler("pause", function () { video.pause(); });
      ms.setActionHandler("seekbackward", function () { seekBy(-10); });
      ms.setActionHandler("seekforward", function () { seekBy(10); });
      ms.setActionHandler("previoustrack", goPrev);
      if (nextUrl) ms.setActionHandler("nexttrack", goNext);
      ms.setActionHandler("seekto", function (d) { video.currentTime = d.seekTime; });
    } catch (e) {}
  }

  window.otubeCurrentTime = function () { return video.currentTime; };

  // ---------- embedded in the mini player: tell the parent page what's playing ----------
  if (video.getAttribute("data-embed") && window.parent !== window) {
    btn("theater").hidden = true;
    var lastPost = 0;
    var post = function (force) {
      var now = Date.now();
      if (!force && now - lastPost < 1000) return;
      lastPost = now;
      window.parent.postMessage({ type: "otube-mini", vid: vid, title: title, t: video.currentTime,
                                  d: dur(), paused: video.paused }, location.origin);
    };
    ["loadedmetadata", "play", "pause", "ended"].forEach(function (ev) { video.addEventListener(ev, function () { post(true); }); });
    video.addEventListener("timeupdate", function () { post(false); });
    window.addEventListener("message", function (e) {
      if (e.origin !== location.origin || !e.data || e.data.type !== "otube-cmd") return;
      if (e.data.cmd === "toggle") togglePlay();
    });

    // Drag the mini player from anywhere on the video (or empty control-bar
    // space): after a few pixels of movement we tell the parent page, which
    // moves the dock. Screen coordinates are the same in both frames.
    var dg = null;
    var sendDrag = function (phase, e) {
      window.parent.postMessage({ type: "otube-drag", phase: phase, x: e.screenX, y: e.screenY }, location.origin);
    };
    wrap.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || e.target.closest("button, input, .p-scrub, .p-menu, .p-upnext")) return;
      dg = { x: e.screenX, y: e.screenY, id: e.pointerId, on: false, t: Date.now() };
    });
    wrap.addEventListener("pointermove", function (e) {
      if (!dg) return;
      if (!dg.on) {
        // forgiving like YouTube: small wobble during a click is still a click
        var dist = Math.hypot(e.screenX - dg.x, e.screenY - dg.y);
        if (dist < 12 || (Date.now() - dg.t < 200 && dist < 20)) return;
        dg.on = true;
        try { wrap.setPointerCapture(dg.id); } catch (err) {}
        wrap.classList.add("p-dragging");
        sendDrag("start", { screenX: dg.x, screenY: dg.y });
      }
      sendDrag("move", e);
    });
    var stopDrag = function (e) {
      if (!dg) return;
      if (dg.on) {
        sendDrag("end", e);
        suppressClick = true;
        setTimeout(function () { suppressClick = false; }, 300);
        wrap.classList.remove("p-dragging");
      }
      dg = null;
    };
    wrap.addEventListener("pointerup", stopDrag);
    wrap.addEventListener("pointercancel", stopDrag);
  }
  syncPlay(); syncTime();
})();
