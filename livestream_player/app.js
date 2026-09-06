/* App shell: connects the MSE player to the engine backend (SSE + playlist),
   or to the offline demo timeline when no backend answers. Handles output
   format switching (new session → re-attach), the chat overlay, gift
   animations, hype meter, and chat-vote card. */

document.addEventListener("DOMContentLoaded", function () {
  var $ = function (id) { return document.getElementById(id); };
  var app = $("app");
  var video = $("feed");
  var feedWrap = $("feedWrap");
  var tapToStart = $("tapToStart");
  var stateLabel = $("stateLabel"), liveLabel = $("liveLabel"), nowPlaying = $("nowPlaying");
  var bufferFill = $("bufferFill"), bufferLabel = $("bufferLabel"), liveDot = $("liveDot");
  var hypeFill = $("hypeFill"), energyLabel = $("energyLabel");
  var compose = $("composeIndicator"), composeText = $("composeText");
  var voteCard = $("voteCard"), votePrompt = $("votePrompt"), voteCount = $("voteCount"), voteTimer = $("voteTimer");
  var giftLayer = $("giftLayer"), chatOverlay = $("chatOverlay"), chatList = $("chatList");
  var engineView = $("engineView"), audienceView = $("audienceView"), worldView = $("worldStateView");
  var jobList = $("jobList"), eventLog = $("eventLog"), queueHint = $("queueHint"), toasts = $("toasts");
  var form = $("promptForm"), input = $("promptInput"), panel = $("sidePanel");
  var togglePanel = $("togglePanel"), toggleChat = $("toggleChat"), toggleAudio = $("toggleAudio");
  var simToggle = $("simToggle"), failOnce = $("failOnce");
  var providerSelect = $("providerSelect"), formatSelect = $("formatSelect"), sizeSelect = $("sizeSelect");

  var viewerId = "v_" + Math.random().toString(36).slice(2, 10);
  var mode = null; // "backend" | "demo"
  var demo = null;
  var failNext = false;
  var lastSeq = 0;
  var lastSnap = null;
  var session = null;
  var player = null;
  var seenEvents = 0;
  var GIFT_EMOJI = { rose: "🌹", heart: "💛", tiktok: "🎵", galaxy: "🌌", lion: "🦁", universe: "🪐", cheer: "💎", superchat: "💬", sub: "⭐", member: "⭐", gift: "🎁" };

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind === "warn" ? " warn" : "");
    el.textContent = msg;
    toasts.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 4200);
  }

  // ------------------------------------------------------------ player mgmt
  function onPlayerEvent(name, detail) {
    if (name === "fallback") toast("MSE unavailable — using clip swap fallback.", "warn");
    if (name === "append-error" || name === "sb-error") console.warn("player", name, detail);
    if (name === "error") console.warn("video error", detail);
  }

  function newVideoElement() {
    var v = document.createElement("video");
    v.id = "feed"; v.muted = true; v.autoplay = true; v.playsInline = true; v.preload = "auto";
    v.poster = app.dataset.format === "portrait" ? "assets/idle_market_portrait.jpg" : "assets/idle_market.jpg";
    return v;
  }

  function resetPlayer(codec) {
    if (player) {
      try { if (player.ms && player.ms.readyState === "open") player.ms.endOfStream(); } catch (e) { /* ignore */ }
      feedWrap.innerHTML = "";
      video = newVideoElement();
      feedWrap.appendChild(video);
      video.addEventListener("play", function () { tapToStart.hidden = true; });
    }
    player = new LivePlayer(video, { onEvent: onPlayerEvent, codec: codec });
    lastSeq = 0;
    return player;
  }

  function ensurePlayer(codec) { return player || resetPlayer(codec); }

  video.addEventListener("play", function () { tapToStart.hidden = true; });
  function tryPlay() {
    var p = video.play();
    if (p && p.catch) p.catch(function () { tapToStart.hidden = false; });
  }
  tapToStart.addEventListener("click", tryPlay);

  var RATIOS = { landscape: 16 / 9, portrait: 9 / 16, square: 1 };
  function fitFrame() {
    var stage = $("stage"), frame = $("frame");
    var r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    var ratio = RATIOS[app.dataset.format] || RATIOS.landscape;
    var w = r.width, h = w / ratio;
    if (h > r.height) { h = r.height; w = h * ratio; }
    frame.style.width = Math.floor(w) + "px";
    frame.style.height = Math.floor(h) + "px";
  }
  window.addEventListener("resize", fitFrame);
  fitFrame();

  function applyFormat(info) {
    if (!info || !info.format) return;
    app.dataset.format = info.format;
    fitFrame();
    if (formatSelect.value !== info.format) formatSelect.value = info.format;
    if (info.size && sizeSelect.value !== info.size) sizeSelect.value = info.size;
    $("backdrop").style.backgroundImage = "url('" + (info.format === "portrait" ? "assets/idle_market_portrait.jpg" : "assets/idle_market.jpg") + "')";
  }

  // --------------------------------------------------------------- render
  function prettyWorld(w) {
    if (!w) return "";
    var chars = (w.characters || []).map(function (c) { return c.name + " — " + c.visual_desc; }).join("\n");
    return ["style  " + w.style_prefix, "place  " + w.setting, "tone   " + w.tone, "camera " + w.current_camera_state,
      "look   " + (w.look || "golden"), "now    " + w.last_established_action, "", chars].join("\n");
  }

  function prettyAudience(a) {
    if (!a) return "";
    return [
      "energy    " + a.energy + " (" + Math.round((a.hype || 0) * 100) + "%)",
      "chat/min  " + a.chat_per_min + "   coins/min " + a.coins_per_min,
      "likes " + a.likes + " · follows " + a.follows + " · shares " + a.shares,
      "top gifts " + ((a.top_gifters || []).map(function (g) { return g.user + " " + g.coins; }).join(", ") || "—"),
      "trending  " + ((a.trending || []).join(", ") || "—"),
      "vote      " + (a.vote_leader ? a.vote_leader.prompt + " (" + a.vote_leader.votes + ")" : "—") + " · " + Math.round(a.vote_window_sec) + "s",
      "sources   " + Object.keys(a.sources || {}).map(function (k) { return k + "=" + a.sources[k]; }).join(" "),
      "",
      (a.log || []).slice(0, 5).join("\n")
    ].join("\n");
  }

  function renderBoard(snap) {
    if (!player) return;
    var f = snap.format || {};
    engineView.textContent = [
      "mode      " + mode + (mode === "backend" ? "" : " (bundled clips, no API)"),
      "output    " + (f.format || "landscape") + " " + (f.width ? f.width + "x" + f.height : "") + (snap.session ? "  session " + snap.session : ""),
      "player    " + player.mode + (player.codec ? "  " + player.codec : ""),
      "provider  " + (snap.primary_provider || "—"),
      "state     " + snap.state,
      "buffer    " + player.totalAhead().toFixed(1) + "s client · " + (snap.buffer_ahead_sec != null ? Number(snap.buffer_ahead_sec).toFixed(1) + "s server" : "—"),
      "loop      " + (snap.active_loop || "—")
    ].join("\n");
    audienceView.textContent = prettyAudience(snap.audience);
    worldView.textContent = prettyWorld(snap.world);
    jobList.innerHTML = "";
    (snap.jobs || []).forEach(function (j) {
      var li = document.createElement("li");
      var meta = j.status + " · " + j.trigger + (j.requested_by && j.requested_by !== "director" ? " · " + j.requested_by : "") + (j.provider ? " · " + j.provider : "") + (j.latency_ms ? " · " + (j.latency_ms / 1000).toFixed(1) + "s" : "");
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

  function renderAudience(a) {
    if (!a) return;
    hypeFill.style.width = Math.max(4, Math.round((a.hype || 0) * 100)) + "%";
    energyLabel.textContent = a.energy || "calm";
    if (a.vote_leader) {
      voteCard.hidden = false;
      votePrompt.textContent = a.vote_leader.prompt;
      voteCount.textContent = a.vote_leader.votes + (a.vote_leader.votes === 1 ? " vote" : " votes");
      voteTimer.textContent = Math.round(a.vote_window_sec) + "s";
    } else {
      voteCard.hidden = true;
    }
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
    if (snap.format) applyFormat(snap.format);
    renderAudience(snap.audience);
    renderBoard(snap);
  }

  function renderBuffer() {
    if (!player) return;
    var buf = player.bufferedAhead();
    bufferFill.style.width = Math.max(4, Math.min(100, (buf / 28) * 100)) + "%";
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

  // ------------------------------------------------------- chat + gifts UI
  function addChat(ev) {
    var li = document.createElement("li");
    if (ev.type === "chat") {
      li.innerHTML = "<span class='p'></span><span class='u'></span><span class='t'></span>";
      li.children[0].textContent = ev.platform === "sim" ? "" : ev.platform;
      li.children[1].textContent = ev.user;
      li.children[2].textContent = ev.text;
    } else if (ev.type === "gift") {
      li.className = "gift";
      li.textContent = ev.user + " sent " + (ev.gift_name || "a gift") + (ev.count > 1 ? " ×" + ev.count : "") + (ev.gift_value ? " (" + ev.gift_value * (ev.count || 1) + ")" : "");
      showGift(ev);
    } else if (ev.type === "follow") {
      li.className = "sys"; li.textContent = ev.user + " followed";
    } else if (ev.type === "share") {
      li.className = "sys"; li.textContent = ev.user + " shared the stream";
    } else if (ev.type === "like") {
      if (Math.random() < 0.5) return;
      li.className = "sys"; li.textContent = ev.user + " liked ×" + (ev.count || 1);
    } else {
      return;
    }
    chatList.appendChild(li);
    while (chatList.children.length > 14) chatList.removeChild(chatList.firstChild);
  }

  function showGift(ev) {
    var total = (ev.gift_value || 1) * (ev.count || 1);
    var tier = total >= 1000 ? "large" : (total >= 100 ? "medium" : "small");
    var key = (ev.gift_name || "gift").toLowerCase();
    var emoji = GIFT_EMOJI.gift;
    Object.keys(GIFT_EMOJI).forEach(function (k) { if (key.indexOf(k) !== -1) emoji = GIFT_EMOJI[k]; });
    var card = document.createElement("div");
    card.className = "gift-card " + tier;
    card.innerHTML = "<span class='emoji'></span><span><div class='who'></div><div class='what'></div></span>";
    card.querySelector(".emoji").textContent = emoji;
    card.querySelector(".who").textContent = ev.user;
    card.querySelector(".what").textContent = "sent " + (ev.gift_name || "a gift") + (ev.count > 1 ? " ×" + ev.count : "") + (tier === "large" ? " — the sky answers" : tier === "medium" ? " — watch the sky" : "");
    giftLayer.appendChild(card);
    setTimeout(function () { if (card.parentNode) card.parentNode.removeChild(card); }, 4300);
    var n = tier === "large" ? 40 : (tier === "medium" ? 18 : 6);
    var rect = giftLayer.getBoundingClientRect();
    for (var i = 0; i < n; i++) {
      var s = document.createElement("i");
      s.className = "spark";
      s.style.left = (rect.width * 0.12 + Math.random() * 60) + "px";
      s.style.top = (rect.height * (tier === "large" ? 0.3 : 0.36) + Math.random() * 30) + "px";
      s.style.setProperty("--dx", (Math.random() * 260 - 60) + "px");
      s.style.setProperty("--dy", (-40 - Math.random() * 220) + "px");
      s.style.animationDelay = (Math.random() * 0.6) + "s";
      giftLayer.appendChild(s);
      (function (el) { setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 2400); })(s);
    }
  }

  // ---------------------------------------------------------- backend mode
  function connectBackend(health) {
    mode = "backend";
    liveLabel.textContent = "Live";
    if (health && health.format) applyFormat(health.format);
    session = health ? health.session : null;
    var es = new EventSource("/api/events");
    es.addEventListener("snapshot", function (e) {
      var snap = JSON.parse(e.data);
      if (session !== null && snap.session !== session) onNewSession(snap.session, snap.format);
      renderState(snap);
    });
    es.addEventListener("segment", function () { pullPlaylist(); });
    es.addEventListener("session", function (e) { var d = JSON.parse(e.data); onNewSession(d.session, d.format); });
    es.addEventListener("audience_event", function (e) { addChat(JSON.parse(e.data).event); });
    es.addEventListener("direction", function (e) {
      var d = JSON.parse(e.data).direction;
      var li = document.createElement("li"); li.className = "sys";
      li.textContent = (d.trigger === "gift" ? "🎁 " : "🗳 ") + "directing: " + d.prompt;
      chatList.appendChild(li);
    });
    es.addEventListener("toast", function (e) { var d = JSON.parse(e.data); toast(d.msg, d.kind); });
    es.onerror = function () { stateLabel.textContent = "RECONNECTING"; };
    pullPlaylist(true);
    setInterval(pullPlaylist, 1000);
    setInterval(function () { fetch("/api/heartbeat/" + viewerId, { method: "POST" }).catch(function () {}); }, 5000);
  }

  function onNewSession(newSession, format) {
    if (newSession === session) return;
    session = newSession;
    applyFormat(format);
    resetPlayer(format && format.codec);
    toast("Output switched to " + (format ? format.format + " " + format.width + "×" + format.height : "new format") + ". Re-attaching…");
    pullPlaylist(true);
  }

  var pulling = false;
  function pullPlaylist(initial) {
    if (pulling) return;
    pulling = true;
    fetch("/api/playlist?after=" + lastSeq)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (session !== null && d.session !== session) { onNewSession(d.session, d.format); return; }
        if (session === null) session = d.session;
        var segs = d.segments || [];
        ensurePlayer(d.codec);
        if ((initial || lastSeq === 0) && d.live_seq) {
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
  function loadDemoManifest(fmt) {
    var MS = window.MediaSource || window.ManagedMediaSource;
    var h264ok = !MS || MS.isTypeSupported('video/mp4; codecs="avc1.4d4028"');
    var dir = (fmt === "portrait" ? "demo_portrait" : "demo") + (h264ok ? "" : "_vp9") + "/";
    var remote = "https://raw.githubusercontent.com/instafire/aether-feed/main/livestream_player/" + dir;
    return fetch(dir + "manifest.json")
      .then(function (r) { if (!r.ok) throw new Error("no local demo"); return r.json().then(function (m) { return { m: m, base: dir }; }); })
      .catch(function () {
        return fetch(remote + "manifest.json").then(function (r) {
          if (!r.ok) throw new Error("no demo manifest for " + dir);
          return r.json().then(function (m) { return { m: m, base: remote }; });
        });
      });
  }

  function startDemo(fmt) {
    loadDemoManifest(fmt)
      .then(function (res) {
        var manifest = res.m;
        var simWas = demo ? demo.simOn : false;
        if (demo) { demo.setSimulator(false); demo.dead = true; }
        applyFormat({ format: manifest.format || fmt, width: manifest.width, height: manifest.height });
        var p = resetPlayer(manifest.codec);
        demo = new DemoTimeline(p, manifest, res.base, { toast: toast, onAudienceEvent: addChat });
        demo.tick();
        tryPlay();
        if (simWas) { demo.setSimulator(true); }
        chatList.innerHTML = "";
      })
      .catch(function (err) {
        toast("No " + fmt + " demo clips available offline; staying on current output.", "warn");
        console.warn(err);
        if (demo) formatSelect.value = app.dataset.format;
      });
  }

  function connectDemo() {
    mode = "demo";
    liveLabel.textContent = "Demo";
    providerSelect.disabled = true;
    sizeSelect.disabled = true;
    startDemo("landscape");
    setInterval(function () { if (demo) { demo.tick(); renderState(demo.snapshot()); } }, 500);
    toast("Offline demo: clips pre-rendered by the engine's mock provider. Run the backend for live generation.");
  }

  fetch("/api/health", { cache: "no-store" })
    .then(function (r) { if (!r.ok) throw new Error("no backend"); return r.json(); })
    .then(function (h) { if (h && h.primary_provider) providerSelect.value = h.primary_provider; connectBackend(h); })
    .catch(connectDemo);

  // ------------------------------------------------------------------- UI
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value.trim();
    if (!v) return;
    if (mode === "demo") {
      var r = demo.submit(v, failNext); failNext = false;
      if (r.ok) input.value = ""; else toast("Give the director a beat.", "warn");
      return;
    }
    fetch("/api/prompt", { method: "POST", headers: { "content-type": "application/json", "x-viewer-id": viewerId },
      body: JSON.stringify({ prompt: v, provider: providerSelect.value }) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (res.ok) { input.value = ""; return; }
        var reason = (res.body && res.body.detail) || "rejected";
        toast(reason === "policy" ? "That request stays off-air. Try a different direction." : reason === "rate" ? "Give the director a beat." : "Type a short direction for this shot.", "warn");
      })
      .catch(function () { toast("Engine unreachable.", "warn"); });
  });

  formatSelect.addEventListener("change", function () {
    var fmt = formatSelect.value;
    if (mode === "demo") { startDemo(fmt); return; }
    fetch("/api/format", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ format: fmt, size: sizeSelect.value }) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.changed) toast("Rebuilding the feed as " + fmt + " " + d.width + "×" + d.height + "…"); })
      .catch(function () { toast("Engine unreachable.", "warn"); });
  });
  sizeSelect.addEventListener("change", function () {
    if (mode !== "backend") return;
    fetch("/api/format", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ format: formatSelect.value, size: sizeSelect.value }) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.changed) toast("Rebuilding the feed at " + d.width + "×" + d.height + "…"); })
      .catch(function () {});
  });

  togglePanel.addEventListener("click", function () {
    var open = panel.hidden; panel.hidden = !open;
    togglePanel.setAttribute("aria-pressed", open ? "true" : "false");
  });
  toggleChat.addEventListener("click", function () {
    var on = toggleChat.getAttribute("aria-pressed") !== "true";
    toggleChat.setAttribute("aria-pressed", on ? "true" : "false");
    chatOverlay.hidden = !on;
  });

  simToggle.addEventListener("click", function () {
    var on = simToggle.getAttribute("aria-pressed") !== "true";
    simToggle.setAttribute("aria-pressed", on ? "true" : "false");
    if (mode === "demo") { if (demo) demo.setSimulator(on); return; }
    fetch("/api/audience/simulate/" + (on ? "on" : "off"), { method: "POST" }).catch(function () {});
  });

  function sendGift(btn) {
    var ev = { platform: "you", type: "gift", user: "you", gift_name: btn.dataset.gift, gift_value: Number(btn.dataset.value), count: 1 };
    if (mode === "demo") { if (demo) demo.ingest(ev); return; }
    fetch("/api/audience/event", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(ev) }).catch(function () {});
  }
  ["sendGift", "sendGiftMid", "sendGiftBig"].forEach(function (id) {
    $(id).addEventListener("click", function () { sendGift(this); });
  });

  var audioCtx = null;
  function startBed() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      var gain = audioCtx.createGain(); gain.gain.value = 0.03; gain.connect(audioCtx.destination);
      var filt = audioCtx.createBiquadFilter(); filt.type = "lowpass"; filt.frequency.value = 380; filt.connect(gain);
      [98, 146.83].forEach(function (f) { var o = audioCtx.createOscillator(); o.type = "sine"; o.frequency.value = f; o.connect(filt); o.start(); });
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  toggleAudio.addEventListener("click", function () {
    var on = toggleAudio.getAttribute("aria-pressed") !== "true";
    toggleAudio.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) startBed(); else if (audioCtx) audioCtx.suspend();
  });

  failOnce.addEventListener("click", function () {
    if (mode === "demo") failNext = true;
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
