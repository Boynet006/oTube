// Plays formats browsers can't open directly (MKV, AVI, TS…) without a converted copy
// on disk: the server repackages the part being watched, a short piece at a time
// (/stream/<id>?start=&len=), and the pieces go into a Media Source buffer at their
// real timestamps. Duration, seeking, buffering and captions then work as usual, and
// everything else (player.js, previews) just uses the <video> element.
(function () {
  "use strict";
  var MS = window.ManagedMediaSource || window.MediaSource;
  var AHEAD = 45, BEHIND = 40;   // seconds kept buffered in front of / behind the playhead

  // otubeStream(video, id, {start}) -> {destroy()}
  window.otubeStream = function (video, id, opts) {
    opts = opts || {};
    var dead = false, ctl = null, url = null;
    function plain() { if (!dead) video.src = "/stream/" + id; }   // continuous stream, no seeking ahead
    fetch("/api/media/" + id).then(function (r) { return r.json(); }).then(function (m) {
      if (dead) return;
      if (m.native) { video.src = "/stream/" + id; return; }
      if (!MS || !MS.isTypeSupported(m.mime)) { plain(); return; }
      run(m);
    }).catch(plain);

    function run(m) {
      var ms = new MS(), sb = null, busy = false, seekTimer = null, nFetched = 0, broken = false;
      if (MS === window.ManagedMediaSource) video.disableRemotePlayback = true;   // required by Safari
      url = URL.createObjectURL(ms);
      video.src = url;
      ms.addEventListener("sourceopen", function () {
        if (sb) return;
        try {
          if (m.dur > 0) ms.duration = m.dur;
          sb = ms.addSourceBuffer(m.mime);
          sb.timestampOffset = -(m.offset || 0);   // pieces carry the file's own timestamps
        } catch (e) { fail(); return; }
        sb.addEventListener("updateend", pump);
        load(Math.max(0, opts.start || 0));
      });

      function fail() {   // something this browser won't take: fall back to the plain stream
        if (broken || dead) return;
        broken = true;
        if (ctl) ctl.abort();
        plain();
        dead = true;
      }
      // the buffered range the playhead is in (small tolerance for gaps between pieces)
      function rangeAt(t) {
        var b = sb.buffered;
        for (var i = 0; i < b.length; i++) if (b.start(i) <= t + 0.3 && b.end(i) > t) return [b.start(i), b.end(i)];
        return null;
      }
      function finish() {
        if (ms.readyState === "open" && !sb.updating) { try { ms.endOfStream(); } catch (e) {} }
      }
      function pump() {
        if (dead || !sb || busy || sb.updating) return;
        var t = video.currentTime, r = rangeAt(t);
        if (r && r[1] - t > AHEAD) { trim(t); return; }
        var from = r ? r[1] : t;
        if (m.dur && from >= m.dur - 0.25) { finish(); return; }
        if (ms.readyState === "ended" && r) return;
        load(from);
      }
      function load(from) {
        busy = true;
        ctl = window.AbortController ? new AbortController() : null;
        var len = nFetched++ ? m.chunk : m.first;   // a short first piece starts playback sooner
        fetch("/stream/" + id + "?start=" + from.toFixed(3) + "&len=" + len, ctl ? { signal: ctl.signal } : {})
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
          .then(function (buf) {
            if (dead) return;
            if (!buf.byteLength) { busy = false; finish(); return; }
            return append(buf).then(function () {
              busy = false;
              if (nFetched === 1 && opts.start && Math.abs(video.currentTime - opts.start) > 1) video.currentTime = opts.start;
              var r = rangeAt(from);
              if (!r || r[1] <= from + 0.05) { finish(); return; }   // nothing new: the real end
              pump();
            });
          })
          .catch(function (e) {
            busy = false;
            if (dead) return;
            if (e && e.name === "AbortError") { pump(); return; }   // aborted for a seek
            if (e && e.append) { fail(); return; }
            setTimeout(pump, 2000);   // network hiccup: try again
          });
      }
      function append(buf) {
        return new Promise(function (ok, bad) {
          function go(retry) {
            function done() { off(); ok(); }
            function err() { off(); var x = new Error("append"); x.append = true; bad(x); }
            function off() { sb.removeEventListener("updateend", done); sb.removeEventListener("error", err); }
            sb.addEventListener("updateend", done);
            sb.addEventListener("error", err);
            try { sb.appendBuffer(buf); }
            catch (e) {
              off();
              if (e.name === "QuotaExceededError" && retry) {   // buffer full: drop what's behind, then retry
                var t = video.currentTime;
                if (t > 5) {
                  sb.addEventListener("updateend", function w() { sb.removeEventListener("updateend", w); go(false); });
                  sb.remove(0, t - 5);
                  return;
                }
              }
              var x = new Error("append"); x.append = true; bad(x);
            }
          }
          if (sb.updating) sb.addEventListener("updateend", function w() { sb.removeEventListener("updateend", w); go(true); });
          else go(true);
        });
      }
      function trim(t) {   // keep memory bounded on long videos
        var b = sb.buffered;
        if (sb.updating || !b.length) return;
        if (b.start(0) < t - BEHIND - 20) sb.remove(0, t - BEHIND);
      }

      video.addEventListener("seeking", function () {
        if (dead || !sb) return;
        clearTimeout(seekTimer);
        if (rangeAt(video.currentTime)) { pump(); return; }
        // somewhere not loaded: wait a moment (dragging the scrubber seeks a lot), then fetch there
        seekTimer = setTimeout(function () {
          if (dead || rangeAt(video.currentTime)) return;
          if (busy && ctl) ctl.abort(); else pump();
        }, 150);
      });
      video.addEventListener("timeupdate", pump);
      video.addEventListener("waiting", pump);
    }

    return {
      destroy: function () {
        dead = true;
        if (ctl) ctl.abort();
        if (url) { URL.revokeObjectURL(url); url = null; }
      }
    };
  };
})();
