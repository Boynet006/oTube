// Plays formats browsers can't open directly (MKV, AVI, TS…) without a converted copy
// on disk: the server repackages the part being watched, a short piece at a time
// (/stream/<id>?start=&len=), and the pieces go into a Media Source buffer at their
// real timestamps. Duration, seeking, buffering and captions then work as usual, and
// everything else (player.js, previews) just uses the <video> element.
(function () {
  "use strict";
  var MS = window.ManagedMediaSource || window.MediaSource;
  var AHEAD = 45, BEHIND = 40;   // seconds kept buffered in front of / behind the playhead
  // H.265 plays in Chrome/Edge/Safari with a hardware decoder: then it's copied, not re-encoded
  var HEVC = !!(MS && MS.isTypeSupported && MS.isTypeSupported('video/mp4; codecs="hvc1.1.6.L150.90"'));
  var Q = HEVC ? "hevc=1" : "";

  var infos = {};   // id + options -> promise of /api/media
  function info(id, q) {
    q = q == null ? Q : q;
    var k = id + "?" + q;
    return infos[k] || (infos[k] = fetch("/api/media/" + id + (q ? "?" + q : ""))
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .catch(function (e) { delete infos[k]; throw e; }));
  }
  function fetchPiece(id, from, len, signal, q) {
    return fetch("/stream/" + id + "?start=" + from.toFixed(3) + "&len=" + len + (q ? "&" + q : ""), signal ? { signal: signal } : {})
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); });
  }

  // Pieces fetched ahead of time (hovering a card or the seek bar), used once when
  // playback gets there: seeks and previews then start without waiting for the server.
  var pieces = [];
  function find(id, from, take, q) {
    q = q == null ? Q : q;
    for (var i = 0; i < pieces.length; i++) {
      var p = pieces[i];
      if (p.id === id && p.q === q && p.start <= from + 0.05 && from < p.start + Math.max(p.len, 4) - 1) {
        if (take) pieces.splice(i, 1);
        return p;
      }
    }
    return null;
  }
  function warm(id, from, q) {
    q = q == null ? Q : q;
    from = Math.max(0, from || 0);
    if (find(id, from, false, q)) return;
    var e = { id: id, q: q, start: from, len: 0, p: null };
    e.p = info(id, q).then(function (m) {
      if (m.native) throw new Error("native");
      e.len = m.first;   // short: cheap if it's never used
      return fetchPiece(id, from, e.len, null, q);
    });
    e.p.catch(function () { var i = pieces.indexOf(e); if (i !== -1) pieces.splice(i, 1); });
    pieces.push(e);
    if (pieces.length > 6) pieces.shift();
  }

  // otubeStream(video, id, {start, audio, burn, force}) -> {destroy(), prefetch(t)}
  //   audio: audio track number; burn: picture-subtitle stream drawn into the video;
  //   force: load an MP4 this way too (needed for audio / burn on MP4s)
  function attach(video, id, opts) {
    opts = opts || {};
    var dead = false, ctl = null, url = null;
    var q = [Q, opts.audio ? "a=" + opts.audio : "", opts.burn != null ? "burn=" + opts.burn : "",
             opts.force ? "force=1" : ""].filter(Boolean).join("&");
    var api = {
      prefetch: function () {},
      destroy: function () {
        dead = true;
        if (ctl) ctl.abort();
        if (url) { URL.revokeObjectURL(url); url = null; }
      }
    };
    function plain() { if (!dead) video.src = "/stream/" + id; }   // continuous stream, no seeking ahead
    info(id, q).then(function (m) {
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
      api.prefetch = function (t) { if (!dead && sb && t >= 0 && !rangeAt(t)) warm(id, t, q); };
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
      function aborted() { var x = new Error("aborted"); x.name = "AbortError"; return x; }
      function load(from) {
        busy = true;
        ctl = window.AbortController ? new AbortController() : null;
        var len = nFetched++ ? m.chunk : m.first;   // a short first piece starts playback sooner
        var hit = find(id, from, true, q), req;
        if (hit) {   // fetched ahead of time; a seek elsewhere still cancels the wait
          var c = ctl;
          req = new Promise(function (ok, bad) {
            hit.p.then(ok, bad);
            if (c) c.signal.addEventListener("abort", function () { bad(aborted()); });
          });
        } else {
          req = fetchPiece(id, from, len, ctl && ctl.signal, q);
        }
        req.then(function (buf) {
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
        if (find(id, video.currentTime, false, q)) { if (busy && ctl) ctl.abort(); else pump(); return; }   // fetched ahead: go now
        // somewhere not loaded: wait a moment (dragging the scrubber seeks a lot), then fetch there
        seekTimer = setTimeout(function () {
          if (dead || rangeAt(video.currentTime)) return;
          if (busy && ctl) ctl.abort(); else pump();
        }, 150);
      });
      video.addEventListener("timeupdate", pump);
      video.addEventListener("waiting", pump);
    }
    return api;
  }

  window.otubeStream = attach;
  attach.warm = warm;
  attach.info = info;
})();
