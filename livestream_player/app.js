document.addEventListener("DOMContentLoaded", function () {
  var camera = document.getElementById("camera");
  var plate = document.getElementById("plate");
  var grade = document.getElementById("grade");
  var fog = document.getElementById("fog");
  var bloom = document.getElementById("bloom");
  var wxCanvas = document.getElementById("wxCanvas");
  var eventCanvas = document.getElementById("eventCanvas");
  var wx = wxCanvas.getContext("2d");
  var ev = eventCanvas.getContext("2d");

  var stateLabel = document.getElementById("stateLabel");
  var nowPlaying = document.getElementById("nowPlaying");
  var bufferFill = document.getElementById("bufferFill");
  var bufferLabel = document.getElementById("bufferLabel");
  var liveDot = document.getElementById("liveDot");
  var compose = document.getElementById("composeIndicator");
  var composeText = document.getElementById("composeText");
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

  var lastUi = 0;
  var camT = 0;
  var lastFrame = performance.now();

  var fx = {
    tod: 0,
    rain: 0,
    fog: 0.14,
    bloom: 0.22,
    grade: 0,
    shake: 0,
    brightness: 1,
    saturate: 1.06,
    contrast: 1.05,
    hue: 0
  };
  var target = {
    tod: 0,
    rain: 0,
    fog: 0.14,
    bloom: 0.22,
    grade: 0,
    shake: 0,
    brightness: 1,
    saturate: 1.06,
    contrast: 1.05,
    hue: 0,
    event: "none"
  };

  var drops = [];
  var dragon = { active: false, x: -0.2, y: 0.28, vx: 0.09, wing: 0, opacity: 0 };

  function resizeCanvases() {
    var r = camera.getBoundingClientRect();
    var dpr = Math.min(1.5, window.devicePixelRatio || 1);
    [wxCanvas, eventCanvas].forEach(function (c) {
      c.width = Math.max(320, r.width * dpr);
      c.height = Math.max(180, r.height * dpr);
    });
  }
  resizeCanvases();
  window.addEventListener("resize", resizeCanvases);

  function seedRain() {
    drops = [];
    var n = 140;
    for (var i = 0; i < n; i++) {
      drops.push({
        x: Math.random(),
        y: Math.random(),
        z: 0.4 + Math.random() * 0.6,
        len: 0.012 + Math.random() * 0.03
      });
    }
  }
  seedRain();

  function applyLook(seg) {
    var look = (seg && seg.fx) || {};
    var tod = look.tod || "golden";
    var weather = look.weather || "clear";
    var event = look.event || "none";

    if (tod === "night") {
      target.tod = 1;
      target.grade = 0.72;
      target.brightness = 0.62;
      target.saturate = 0.78;
      target.contrast = 1.12;
      target.hue = 12;
      target.bloom = 0.08;
      target.fog = 0.28;
    } else if (tod === "storm") {
      target.tod = 0.55;
      target.grade = 0.55;
      target.brightness = 0.7;
      target.saturate = 0.55;
      target.contrast = 1.18;
      target.hue = -8;
      target.bloom = 0.04;
      target.fog = 0.32;
    } else {
      target.tod = 0;
      target.grade = 0.0;
      target.brightness = 1;
      target.saturate = 1.06;
      target.contrast = 1.05;
      target.hue = 0;
      target.bloom = 0.22;
      target.fog = 0.14;
    }

    if (weather === "rain") {
      target.rain = 1;
      target.shake = 0.55;
      target.fog = Math.max(target.fog, 0.3);
    } else {
      target.rain = 0;
      target.shake = 0;
    }

    target.event = event;
    if (event === "dragon" && !dragon.active) {
      dragon.active = true;
      dragon.x = -0.18;
      dragon.y = 0.26;
      dragon.vx = 0.085;
      dragon.opacity = 0;
    }
  }

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function drawDragon(ctx, w, h, t) {
    if (!dragon.active && dragon.opacity < 0.01) {
      ctx.clearRect(0, 0, w, h);
      return;
    }
    if (target.event === "dragon") {
      dragon.opacity = Math.min(1, dragon.opacity + t * 0.6);
      dragon.x += dragon.vx * t;
      if (dragon.x > 0.52) {
        dragon.vx *= 0.96;
        dragon.x = Math.min(dragon.x, 0.58);
      }
    } else {
      dragon.opacity = Math.max(0, dragon.opacity - t * 0.35);
      dragon.x += 0.03 * t;
      if (dragon.opacity <= 0.01) dragon.active = false;
    }
    dragon.wing += t * 7;
    dragon.y = 0.26 + Math.sin(camT * 0.7) * 0.012;

    ctx.clearRect(0, 0, w, h);
    if (dragon.opacity <= 0) return;
    ctx.save();
    ctx.globalAlpha = dragon.opacity * 0.92;
    ctx.translate(dragon.x * w, dragon.y * h);
    var s = Math.min(w, h) * 0.22;
    ctx.scale(s / 100, s / 100);
    var flap = Math.sin(dragon.wing) * 18;
    ctx.fillStyle = "rgba(92, 48, 18, 0.92)";
    ctx.beginPath();
    ctx.moveTo(-70, 8);
    ctx.quadraticCurveTo(-10 + flap, -70, 10, -8);
    ctx.quadraticCurveTo(-8, -18, -70, 8);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(-20, 10);
    ctx.quadraticCurveTo(40, -55 + flap * 0.4, 86, 6);
    ctx.quadraticCurveTo(30, -6, -20, 10);
    ctx.fill();
    ctx.fillStyle = "rgba(176, 96, 32, 0.95)";
    ctx.beginPath();
    ctx.ellipse(8, 10, 38, 14, -0.25, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(40, 6);
    ctx.lineTo(62, -4);
    ctx.lineTo(58, 10);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "rgba(40, 18, 8, 0.9)";
    ctx.beginPath();
    ctx.arc(48, 4, 2.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawRain(ctx, w, h, intensity, dt) {
    ctx.clearRect(0, 0, w, h);
    if (intensity < 0.02) return;
    ctx.strokeStyle = "rgba(220,230,240," + (0.18 + intensity * 0.35) + ")";
    ctx.lineWidth = 1.1;
    var wind = 0.12 + intensity * 0.18;
    for (var i = 0; i < drops.length; i++) {
      var d = drops[i];
      d.y += (0.55 + d.z * 0.9) * intensity * dt * 1.6;
      d.x += wind * intensity * dt * 0.25;
      if (d.y > 1.05) {
        d.y = -0.05;
        d.x = Math.random();
      }
      if (d.x > 1.05) d.x -= 1.1;
      var x = d.x * w;
      var y = d.y * h;
      var len = d.len * h * (0.7 + intensity);
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + wind * 40, y + len);
      ctx.stroke();
    }
    if (intensity > 0.4) {
      ctx.fillStyle = "rgba(180,200,220," + (intensity * 0.06) + ")";
      ctx.fillRect(0, 0, w, h);
    }
  }

  function compositor(ts) {
    var dt = Math.min(0.05, (ts - lastFrame) / 1000);
    lastFrame = ts;
    camT += dt;

    var k = 1 - Math.pow(0.08, dt);
    Object.keys(fx).forEach(function (key) {
      fx[key] = lerp(fx[key], target[key], k);
    });

    var driftX = Math.sin(camT * 0.07) * 2.4;
    var driftY = Math.cos(camT * 0.045) * 1.5;
    var zoom = 1.08 + Math.sin(camT * 0.033) * 0.035;
    var shakeX = Math.sin(camT * 18) * fx.shake * 0.18;
    var shakeY = Math.cos(camT * 21) * fx.shake * 0.12;
    camera.style.transform =
      "translate3d(" + (driftX + shakeX) + "%, " + (driftY + shakeY) + "%, 0) scale(" + zoom + ")";

    plate.style.filter =
      "brightness(" + fx.brightness + ") saturate(" + fx.saturate +
      ") contrast(" + fx.contrast + ") hue-rotate(" + fx.hue + "deg)";

    grade.style.opacity = String(fx.grade);
    if (target.tod > 0.7) {
      grade.style.background = "linear-gradient(180deg, #0b1220 0%, #1a2438 55%, #3a2a18 100%)";
    } else if (target.tod > 0.3) {
      grade.style.background = "linear-gradient(180deg, #2a3340 0%, #3a3a38 100%)";
    } else {
      grade.style.background = "#1a2438";
    }
    fog.style.opacity = String(fx.fog);
    bloom.style.opacity = String(fx.bloom);

    drawRain(wx, wxCanvas.width, wxCanvas.height, fx.rain, dt);
    drawDragon(ev, eventCanvas.width, eventCanvas.height, dt);

    requestAnimationFrame(compositor);
  }

  function present(seg) {
    applyLook(seg);
  }

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind === "warn" ? " warn" : "");
    el.textContent = msg;
    toasts.appendChild(el);
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 4200);
  }

  function prettyWorld(w) {
    var chars = (w.characters || [])
      .map(function (c) { return c.name + " — " + c.visual_desc; })
      .join("\n");
    return [
      "style  " + w.style_prefix,
      "place  " + w.setting,
      "tone   " + w.tone,
      "camera " + w.current_camera_state,
      "tod    " + (w.tod || "golden"),
      "wx     " + (w.weather || "clear"),
      "now    " + w.last_established_action,
      "",
      chars
    ].join("\n");
  }

  function renderBoard(snap) {
    worldView.textContent = prettyWorld(snap.world);
    jobList.innerHTML = "";
    snap.jobs.forEach(function (j) {
      var li = document.createElement("li");
      li.innerHTML =
        "<div class='st'>" + j.status + " · " + j.trigger + "</div>" +
        "<div>" + (j.user_prompt_raw || "") + "</div>";
      jobList.appendChild(li);
    });
    eventLog.innerHTML = "";
    snap.events.forEach(function (e) {
      var li = document.createElement("li");
      li.textContent = e.msg;
      eventLog.appendChild(li);
    });
  }

  function onState(snap) {
    stateLabel.textContent = snap.state;
    nowPlaying.textContent = snap.nowPlaying || "locked camera";
    var composing =
      snap.state === "PROMPT_QUEUED" ||
      snap.state === "GENERATING_NEW_LOOP" ||
      snap.state === "SPLICING" ||
      snap.jobs.some(function (j) { return j.status === "running"; });
    compose.hidden = !composing;
    if (snap.state === "GENERATING_NEW_LOOP") composeText.textContent = "holding the shot";
    else if (snap.state === "SPLICING") composeText.textContent = "weather is turning";
    else if (snap.state === "ERROR_RECOVERY") composeText.textContent = "holding — recovering";
    else composeText.textContent = "composing next beat";

    var q = snap.queue.length;
    queueHint.textContent = q ? q + " waiting" : "";
    renderBoard(snap);
  }

  function onTick(snap) {
    var t = performance.now();
    if (t - lastUi < 180) return;
    lastUi = t;
    var buf = snap.bufferAhead;
    var pct = Math.max(4, Math.min(100, (buf / 28) * 100));
    bufferFill.style.width = pct + "%";
    bufferFill.classList.toggle("low", buf < 8 && buf >= 3.5);
    bufferFill.classList.toggle("critical", buf < 3.5);
    bufferLabel.textContent = buf.toFixed(1) + "s";
    liveDot.classList.toggle("warn", buf < 8);
    liveDot.classList.toggle("bad", snap.state === "ERROR_RECOVERY");
  }

  var preload = new Image();
  preload.src = "assets/idle_market.jpg";

  var engine = new LivestreamEngine.Engine({
    ui: {
      present: present,
      onState: onState,
      onTick: onTick,
      onLog: function () {},
      toast: toast
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = input.value;
    var res = engine.submitPrompt(v);
    if (res.ok) input.value = "";
  });

  togglePanel.addEventListener("click", function () {
    var open = panel.hidden;
    panel.hidden = !open;
    togglePanel.setAttribute("aria-pressed", open ? "true" : "false");
  });

  var audioCtx = null;
  var gain = null;
  function startBed() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      gain = audioCtx.createGain();
      gain.gain.value = 0.03;
      gain.connect(audioCtx.destination);
      var oscA = audioCtx.createOscillator();
      var oscB = audioCtx.createOscillator();
      oscA.type = "sine";
      oscB.type = "sine";
      oscA.frequency.value = 98;
      oscB.frequency.value = 146.83;
      var filt = audioCtx.createBiquadFilter();
      filt.type = "lowpass";
      filt.frequency.value = 380;
      oscA.connect(filt);
      oscB.connect(filt);
      filt.connect(gain);
      oscA.start();
      oscB.start();
    }
    if (audioCtx.state === "suspended") audioCtx.resume();
  }
  function stopBed() {
    if (audioCtx && audioCtx.state === "running") audioCtx.suspend();
  }

  toggleAudio.addEventListener("click", function () {
    engine.audioOn = !engine.audioOn;
    toggleAudio.setAttribute("aria-pressed", engine.audioOn ? "true" : "false");
    if (engine.audioOn) startBed();
    else stopBed();
  });

  failOnce.addEventListener("click", function () {
    engine.failNext = true;
    toast("Next generation will fail — this shot holds.", "warn");
  });

  providerSelect.addEventListener("change", function () {
    engine.provider = providerSelect.value;
    toast("Provider set to " + engine.provider + ". Camera stays locked.");
  });

  requestAnimationFrame(compositor);
  engine.start();
  onState(engine.snapshot());
});
