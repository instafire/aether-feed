/* App shell: connects the MSE player to the engine backend (SSE + playlist),
   or to the offline demo timeline when no backend answers. */

document.addEventListener("DOMContentLoaded", function () {
  var video = document.getElementById("feed");
  var tapToStart = document.getElementById("tapToStart");
  var stateLabel = document.getElementById("stateLabel");
  var liveLabel = document.getElementById("liveLabel");
  var nowPlaying = document.getElementById("nowPlaying");
  var bufferFill = document.getElementById("bufferFill");
  var bufferLabel = document.getElementById("bufferLabel");
  var liveDot = document.getElementById("liveDot");
  var compose = document.getElementById("composeIndicator");
  var composeText = document.getElementById("composeText");
  var engineView = document.getElementById("engineView");
  var worldView = document.getElementById("worldStateView");
  var jobList = document.getElementById("jobList");
  var eventLog = document.getElementById("eventLog");
  var queueHint = document.getElementById("queueHint");
  var toasts = document.getElementById("toasts");
  var form = document.getElementById("promptForm");
  var input = document.getElementById("promptInput");
  var panel = document.getElementById("sidePanel");
  var togglePanel = document.getElementById("togglePanel");
  var toggleAudio = document.getElementById("toggleAudio");
  var failOnce = document.getElementById("failOnce");
  var providerSelect = document.getElementById("providerSelect");

  var viewerId = "v_" + Math.random().toString(36).slice(2, 10);
  var mode = null; // "backend" | "demo"
  var demo = null;
  var failNext = false;
  var lastSeq = 0;
  var lastSnap = null;
  var player = null;

  function ensurePlayer(codec) {
    if (player) return player;
    player = new LivePlayer(video, { onEvent: onPlayerEvent, codec: codec });
    return player;
  }

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind === "warn" ? " warn" : "");
    el.textContent = msg;
    toasts.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 4200);
  }

  function onPlayerEvent(name, detail) {
    if (name === "fallback") toast("MSE unavailable — using clip swap fallback.", "warn");
    if (name === "append-error" || name === "sb-error") console.warn("player", name, detail);
    if (name === "error") console.warn("video error", detail);
  }

  video.addEventListener("play", function () { tapToStart.hidden = true; });
  function tryPlay() {
    var p = video.play();
    if (p && p.catch) p.catch(function () { tapToStart.hidden = false; });
  }
  tapToStart.addEventListener("click", function () { tryPlay(); });

  // ------------------------------------------------------------- rendering
  function prettyWorld(w) {
    if (!w) return "";
    var chars = (w.characters || []).map(function (c) { return c.name + " — " + c.visual_desc; }).join("\n");
    return [
      "style  " + w.style_prefix,
      "place  " + w.setting,
      "tone   " + w.tone,
      "camera " + w.current_camera_state,
      "look   " + (w.look || "golden"),
      "now    " + w.last_established_action,
      "frame  " + (w.last_frame_description || ""),
      "",
      chars
    ].join("\n");
  }

  function renderBoard(snap) {
    if (!player) return;
    engineView.textContent = [
      "mode      " + mode + (mode === "backend" ? "" : " (bundled clips, no API)"),
      "player    " + player.mode + (player.codec ? "  " + player.codec : ""),
      "provider  " + (snap.primary_provider || "—"),
      "state     " + snap.state,
      "buffer    " + player.totalAhead().toFixed(1) + "s client · " + (snap.buffer_ahead_sec != null ? Number(snap.buffer_ahead_sec).toFixed(1) + "s server" : "—"),
      "live      " + (snap.live_offset_sec != null ? "+" + Number(snap.live_offset_sec).toFixed(0) + "s" : "—"),
      "loop      " + (snap.active_loop || "—")
    ].join("\n");
    worldView.textContent = prettyWorld(snap.world);
    jobList.innerHTML = "";
    (snap.jobs || []).forEach(function (j) {
      var li = document.createElement("li");
      var meta = j.status + " · " + j.trigger + (j.provider ? " · " + j.provider : "") + (j.latency_ms ? " · " + (j.latency_ms / 1000).toFixed(1) + "s" : "");
      li.innerHTML = "<div class='st'></div><div></div><div class='err'></div>";
      li.children[0].textContent = meta;
      li.children[1].textContent = j.user_prompt_raw || "";
      li.children[2].textContent = j.error || "";
      jobList.appendChild(li);
    });
    eventLog.innerHTML = "";
    (snap.events || []).forEach(function (e) {
      var li = document.createElement("li");
      li.textContent = typeof e === "string" ? e : e.msg;
      eventLog.appendChild(li);
    });
  }

  function renderState(snap) {
    lastSnap = snap;
    stateLabel.textContent = snap.state;
    var cur = player ? player.currentItem() : null;
    nowPlaying.textContent = mode === "backend" ? (snap.now_playing || "—") : ((cur && cur.title) || snap.now_playing || "—");
    var running = (snap.jobs || []).some(function (j) { return j.status === "running" && j.trigger !== "library"; });
    var composing = snap.state === "PROMPT_QUEUED" || snap.state === "GENERATING_NEW_LOOP" || snap.state === "SPLICING" || running;
    compose.hidden = !composing;
    if (snap.state === "GENERATING_NEW_LOOP") composeText.textContent = "anchoring a new idle loop";
    else if (snap.state === "SPLICING") composeText.textContent = "next beat is in the buffer";
    else if (snap.state === "ERROR_RECOVERY") composeText.textContent = "holding — recovering";
    else composeText.textContent = "composing next beat";
    var q = (snap.queue || []).length;
    queueHint.textContent = q ? q + " waiting" : "";
    liveDot.classList.toggle("bad", snap.state === "ERROR_RECOVERY");
    renderBoard(snap);
  }

  function renderBuffer() {
    if (!player) return;
    var buf = player.bufferedAhead();
    var pct = Math.max(4, Math.min(100, (buf / 28) * 100));
    bufferFill.style.width = pct + "%";
    bufferFill.classList.toggle("low", buf < 6 && buf >= 2.5);
    bufferFill.classList.toggle("critical", buf < 2.5);
    bufferLabel.textContent = buf.toFixed(1) + "s";
    liveDot.classList.toggle("warn", buf < 6);
    if (mode === "demo") {
      var cur = player.currentItem();
      if (cur && cur.title) nowPlaying.textContent = cur.title;
    }
    player.nudge();
  }
  setInterval(renderBuffer, 250);

  // ---------------------------------------------------------- backend mode
  function connectBackend() {
    mode = "backend";
    liveLabel.textContent = "Live";
    var es = new EventSource("/api/events");
    es.addEventListener("snapshot", function (e) { renderState(JSON.parse(e.data)); });
    es.addEventListener("segment", function () { pullPlaylist(); });
    es.addEventListener("toast", function (e) { var d = JSON.parse(e.data); toast(d.msg, d.kind); });
    es.addEventListener("job", function () { /* snapshot covers it */ });
    es.onerror = function () { stateLabel.textContent = "RECONNECTING"; };
    pullPlaylist(true);
    setInterval(pullPlaylist, 1000);
    setInterval(function () {
      fetch("/api/heartbeat/" + viewerId, { method: "POST" }).catch(function () {});
    }, 5000);
  }

  var pulling = false;
  function pullPlaylist(initial) {
    if (pulling) return;
    pulling = true;
    fetch("/api/playlist?after=" + lastSeq)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var segs = d.segments || [];
        ensurePlayer(d.codec);
        if (initial && lastSeq === 0 && d.live_seq) {
          // Join at the live edge: skip everything already behind the playhead.
          segs = segs.filter(function (s) { return s.seq >= d.live_seq; });
        }
        segs.forEach(function (s) {
          if (s.seq <= lastSeq) return;
          lastSeq = s.seq;
          player.enqueue({ url: s.url, seq: s.seq, duration: s.duration_sec, title: s.title, source: s.source });
        });
        if (segs.length) tryPlay();
      })
      .catch(function () {})
      .then(function () { pulling = false; });
  }

  // ------------------------------------------------------------- demo mode
  function connectDemo() {
    mode = "demo";
    liveLabel.textContent = "Demo";
    providerSelect.value = "mock";
    providerSelect.disabled = true;
    // Local demo folder first; hosted builds that can't ship .mp4 stream the
    // same clips from the repo.
    // Two encodes of the same graph: H.264 fMP4 (default) and VP9 WebM for
    // browsers without an H.264 decoder.
    var MS = window.MediaSource || window.ManagedMediaSource;
    var h264ok = !MS || MS.isTypeSupported('video/mp4; codecs="avc1.4d4028"');
    var dir = h264ok ? "demo/" : "demo_vp9/";
    var REMOTE_DEMO = "https://raw.githubusercontent.com/instafire/aether-feed/main/livestream_player/" + dir;
    var base = dir;
    fetch(dir + "manifest.json")
      .then(function (r) { if (!r.ok) throw new Error("no local demo"); return r.json(); })
      .catch(function () {
        base = REMOTE_DEMO;
        return fetch(REMOTE_DEMO + "manifest.json").then(function (r) {
          if (!r.ok) throw new Error("no demo manifest");
          return r.json();
        });
      })
      .then(function (manifest) {
        demo = new DemoTimeline(ensurePlayer(manifest.codec), manifest, base, { toast: toast });
        demo.tick();
        tryPlay();
        setInterval(function () { demo.tick(); renderState(demo.snapshot()); }, 500);
        toast("Offline demo: clips pre-rendered by the engine's mock provider. Run the backend for live generation.");
      })
      .catch(function (err) {
        stateLabel.textContent = "NO FEED";
        toast("No engine and no demo clips found. Run: uvicorn app.main:app", "warn");
        console.warn(err);
      });
  }

  fetch("/api/health", { cache: "no-store" })
    .then(function (r) { if (!r.ok) throw new Error("no backend"); return r.json(); })
    .then(function (h) {
      if (h && h.primary_provider) providerSelect.value = h.primary_provider;
      connectBackend();
    })
    .catch(connectDemo);

  // ------------------------------------------------------------------- UI
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value.trim();
    if (!v) return;
    if (mode === "demo") {
      var r = demo.submit(v, failNext);
      failNext = false;
      if (r.ok) input.value = "";
      else toast("Give the director a beat.", "warn");
      return;
    }
    fetch("/api/prompt", {
      method: "POST",
      headers: { "content-type": "application/json", "x-viewer-id": viewerId },
      body: JSON.stringify({ prompt: v, provider: providerSelect.value })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (res.ok) { input.value = ""; return; }
        var reason = (res.body && res.body.detail) || "rejected";
        toast(reason === "policy" ? "That request stays off-air. Try a different direction." :
              reason === "rate" ? "Give the director a beat." : "Type a short direction for this shot.", "warn");
      })
      .catch(function () { toast("Engine unreachable.", "warn"); });
  });

  togglePanel.addEventListener("click", function () {
    var open = panel.hidden;
    panel.hidden = !open;
    togglePanel.setAttribute("aria-pressed", open ? "true" : "false");
  });

  var audioCtx = null;
  function startBed() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var gain = audioCtx.createGain();
      gain.gain.value = 0.03;
      gain.connect(audioCtx.destination);
      var filt = audioCtx.createBiquadFilter();
      filt.type = "lowpass";
      filt.frequency.value = 380;
      filt.connect(gain);
      [98, 146.83].forEach(function (f) {
        var o = audioCtx.createOscillator();
        o.type = "sine";
        o.frequency.value = f;
        o.connect(filt);
        o.start();
      });
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  toggleAudio.addEventListener("click", function () {
    var on = toggleAudio.getAttribute("aria-pressed") !== "true";
    toggleAudio.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) startBed(); else if (audioCtx) audioCtx.suspend();
  });

  failOnce.addEventListener("click", function () {
    if (mode === "demo") { failNext = true; }
    else fetch("/api/fail-next", { method: "POST" }).catch(function () {});
    toast("Next generation will fail — the shot should hold.", "warn");
  });

  providerSelect.addEventListener("change", function () {
    if (mode !== "backend") return;
    fetch("/api/provider/" + providerSelect.value, { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () {
        var p = lastSnap && lastSnap.providers && lastSnap.providers[providerSelect.value];
        toast(p && !p.configured ? providerSelect.value + " has no API key — mock fallback will render." : "Provider set to " + providerSelect.value + ".");
      })
      .catch(function () {});
  });
});
