/* LivePlayer — gap-free clip chaining with Media Source Extensions.
   One <video>, one SourceBuffer in 'sequence' mode: each fragmented MP4 we
   append is stitched onto the end of the previous one, frame-accurate,
   with no element swap and no decoder reset (spec §5.1, option 1).
   Falls back to a double-buffered <video> swap where MSE is unavailable. */

(function (global) {
  "use strict";

  var DEFAULT_CODEC = 'video/mp4; codecs="avc1.4d4028"';
  var CODEC_CANDIDATES = [
    'video/mp4; codecs="avc1.4d4028"',
    'video/mp4; codecs="avc1.4d401f"',
    'video/mp4; codecs="avc1.64001f"',
    'video/mp4; codecs="avc1.42E01E"',
    'video/webm; codecs="vp9"'
  ];

  function pickCodec(preferred) {
    var MS = global.MediaSource || global.ManagedMediaSource;
    if (!MS) return null;
    // The stream is encoded in ONE codec; only accept what the server sent
    // (or its close H.264 profile variants). Falling back to a different
    // codec would just fail at append time.
    var list = preferred ? [preferred] : CODEC_CANDIDATES;
    if (preferred && preferred.indexOf("mp4") !== -1) list = list.concat(CODEC_CANDIDATES.slice(0, 4));
    for (var i = 0; i < list.length; i++) {
      if (MS.isTypeSupported(list[i])) return list[i];
    }
    return null;
  }

  function LivePlayer(video, opts) {
    this.video = video;
    this.opts = opts || {};
    this.codec = pickCodec(this.opts.codec || DEFAULT_CODEC);
    this.mode = this.codec ? "mse" : "swap";
    this.queue = [];
    this.appended = [];
    this.appending = false;
    this.ms = null;
    this.sb = null;
    this.ready = false;
    this.onEvent = this.opts.onEvent || function () {};
    this.stalledSince = 0;
    this._init();
  }

  LivePlayer.prototype._init = function () {
    var self = this;
    var v = this.video;
    v.muted = true;
    v.playsInline = true;
    v.autoplay = true;
    v.loop = false;

    v.addEventListener("waiting", function () { self.stalledSince = performance.now(); self.onEvent("waiting"); });
    v.addEventListener("playing", function () { self.stalledSince = 0; self.onEvent("playing"); });
    v.addEventListener("error", function () { self.onEvent("error", v.error); });

    if (this.mode !== "mse") {
      this._initSwap();
      return;
    }
    var MS = global.MediaSource || global.ManagedMediaSource;
    this.ms = new MS();
    if (global.ManagedMediaSource && this.ms instanceof global.ManagedMediaSource) {
      v.disableRemotePlayback = true;
    }
    this.ms.addEventListener("sourceopen", function () {
      if (self.sb) return;
      try {
        self.sb = self.ms.addSourceBuffer(self.codec);
        self.sb.mode = "sequence";
        self.sb.addEventListener("updateend", function () {
          self.appending = false;
          self._trim();
          self._pump();
        });
        self.sb.addEventListener("error", function (e) { self.onEvent("sb-error", e); self.appending = false; });
        self.ready = true;
        self.onEvent("ready");
        self._pump();
      } catch (err) {
        self.mode = "swap";
        self.onEvent("fallback", err);
        self._initSwap();
      }
    });
    v.src = URL.createObjectURL(this.ms);
  };

  LivePlayer.prototype.enqueue = function (item) {
    // item: { url, seq, duration, title, source }
    this.queue.push(item);
    this._pump();
  };

  LivePlayer.prototype._pump = function () {
    var self = this;
    if (this.mode !== "mse") { this._pumpSwap(); return; }
    if (!this.ready || this.appending || !this.queue.length) return;
    if (this.sb.updating) return;
    var item = this.queue.shift();
    this.appending = true;
    fetch(item.url, { cache: "force-cache" })
      .then(function (r) { if (!r.ok) throw new Error("fetch " + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        try {
          self.sb.appendBuffer(buf);
          self.appended.push(item);
          self.onEvent("appended", item);
          if (self.video.paused) self.video.play().catch(function () {});
        } catch (err) {
          self.appending = false;
          if (err && err.name === "QuotaExceededError") {
            self.queue.unshift(item);
            self._trim(true);
          } else {
            self.onEvent("append-error", err);
          }
        }
      })
      .catch(function (err) {
        self.appending = false;
        self.onEvent("fetch-error", err);
        setTimeout(function () { self.queue.unshift(item); self._pump(); }, 800);
      });
  };

  LivePlayer.prototype._trim = function (force) {
    if (!this.sb || this.sb.updating) return;
    var v = this.video;
    var b = v.buffered;
    if (!b.length) return;
    var start = b.start(0);
    var keep = force ? 8 : 30;
    var cut = v.currentTime - keep;
    if (cut - start > 10) {
      try { this.sb.remove(start, cut); } catch (e) { /* ignore */ }
    }
  };

  LivePlayer.prototype.bufferedAhead = function () {
    var v = this.video;
    var b = v.buffered;
    if (!b.length) return 0;
    var end = b.end(b.length - 1);
    return Math.max(0, end - v.currentTime);
  };

  LivePlayer.prototype.queuedSeconds = function () {
    var s = 0;
    for (var i = 0; i < this.queue.length; i++) s += this.queue[i].duration || 0;
    return s;
  };

  LivePlayer.prototype.totalAhead = function () {
    return this.bufferedAhead() + this.queuedSeconds();
  };

  LivePlayer.prototype.nudge = function () {
    // Recover from a stall that outlived the network: jump to the buffered start.
    var v = this.video;
    if (this.stalledSince && performance.now() - this.stalledSince > 2500 && v.buffered.length) {
      var end = v.buffered.end(v.buffered.length - 1);
      if (end - v.currentTime > 0.5) {
        v.currentTime = Math.min(v.currentTime + 0.25, end - 0.1);
        v.play().catch(function () {});
      }
    }
  };

  LivePlayer.prototype.currentItem = function () {
    // Which appended item is playing? Walk cumulative durations from the
    // first buffered start (sequence mode = contiguous).
    // Sequence mode lays clips out from t=0 in append order, so the absolute
    // currentTime maps straight onto cumulative durations (trimming old
    // ranges doesn't shift it).
    var v = this.video;
    if (!this.appended.length) return null;
    var t = v.currentTime;
    var trimmed = 0;
    for (var i = 0; i < this.appended.length; i++) {
      var d = this.appended[i].duration || 0;
      if (t < trimmed + d) return this.appended[i];
      trimmed += d;
    }
    return this.appended[this.appended.length - 1];
  };

  // ------------------------------------------------------------ swap fallback
  LivePlayer.prototype._initSwap = function () {
    var self = this;
    this.ready = true;
    this.swapNext = document.createElement("video");
    this.swapNext.muted = true;
    this.swapNext.playsInline = true;
    this.swapNext.preload = "auto";
    this.swapNext.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;";
    this.video.parentNode.appendChild(this.swapNext);
    this.video.addEventListener("ended", function () { self._swap(); });
    this.onEvent("ready");
  };

  LivePlayer.prototype._pumpSwap = function () {
    if (!this.queue.length) return;
    if (!this.video.src) {
      var first = this.queue.shift();
      this.video.src = first.url;
      this.appended.push(first);
      this.video.play().catch(function () {});
    }
    if (!this.swapNext.src && this.queue.length) {
      this.swapNext.src = this.queue[0].url;
      this.swapNext.load();
    }
  };

  LivePlayer.prototype._swap = function () {
    if (!this.queue.length) { this.video.currentTime = 0; this.video.play().catch(function () {}); return; }
    var item = this.queue.shift();
    var a = this.video;
    var b = this.swapNext;
    b.src = item.url;
    b.style.opacity = "1";
    a.style.opacity = "0";
    b.play().catch(function () {});
    this.video = b;
    this.swapNext = a;
    a.src = "";
    this.appended.push(item);
    var self = this;
    b.onended = function () { self._swap(); };
    if (this.queue.length) { a.src = this.queue[0].url; a.load(); }
  };

  global.LivePlayer = LivePlayer;
})(window);
