/* Continuous generative livestream — locked-camera mock.
   One plate. The playhead never swaps shots. Prompts morph
   weather, time of day, and in-frame events on the same camera. */

(function (global) {
  "use strict";

  var STATES = {
    IDLE_LOOP: "IDLE_LOOP",
    PROMPT_QUEUED: "PROMPT_QUEUED",
    SPLICING: "SPLICING",
    PLAYING_GENERATED: "PLAYING_GENERATED",
    GENERATING_NEW_LOOP: "GENERATING_NEW_LOOP",
    BUFFER_LOW: "BUFFER_LOW",
    ERROR_RECOVERY: "ERROR_RECOVERY"
  };

  var TARGET_BUFFER = 22;
  var LOW_BUFFER = 8;
  var CRITICAL_BUFFER = 3.5;
  var SEGMENT_SEC = 8.0;
  var RATE_LIMIT_MS = 2500;
  var PLATE = "assets/idle_market.jpg";

  var STYLE_PREFIX =
    "cinematic, 35mm film grain, locked wide on a floating market, warm golden-hour color grade";

  var DEFAULT_WORLD = {
    style_prefix: STYLE_PREFIX,
    setting: "a floating market town above the clouds, camera locked on the rope bridge",
    characters: [
      {
        name: "the fox-eared courier",
        visual_desc: "orange fur, blue traveling cloak, brass goggles"
      }
    ],
    current_camera_state: "locked wide, slow handheld drift, never cuts",
    last_established_action: "lanterns sway over the market walkway at golden hour",
    last_frame_description:
      "the same wide shot of a floating wooden market above clouds, sun low left, rope bridge center",
    tone: "whimsical, adventurous",
    tod: "golden",
    weather: "clear",
    event: "none",
    updated_at: new Date().toISOString()
  };

  var IDLE_FX = {
    tod: "golden",
    weather: "clear",
    event: "none",
    title: "locked camera · golden hour"
  };

  var SCENE_BANK = [
    {
      id: "dragon",
      keys: ["dragon", "wyrm", "lands", "landing", "beast", "wings", "flies"],
      fx: { tod: "golden", weather: "clear", event: "dragon", title: "a dragon crosses the frame" },
      last_frame:
        "same locked wide: an amber dragon glides across the rope bridge in golden light",
      world_patch: {
        last_established_action: "an amber dragon is crossing the market bridge",
        event: "dragon"
      }
    },
    {
      id: "night",
      keys: ["night", "star", "dusk", "evening", "dark", "moon", "twilight"],
      fx: { tod: "night", weather: "clear", event: "none", title: "night falls in-camera" },
      last_frame:
        "same locked wide: night over the floating market, lanterns holding the light",
      world_patch: {
        last_established_action: "night has fallen over this same shot",
        tod: "night",
        tone: "hushed, wondrous"
      }
    },
    {
      id: "rain",
      keys: ["rain", "storm", "thunder", "wet", "wind", "downpour"],
      fx: { tod: "storm", weather: "rain", event: "none", title: "rain on the boards" },
      last_frame:
        "same locked wide: rain sheets across the market boards and the cloud sea",
      world_patch: {
        last_established_action: "a storm has rolled through this same shot",
        weather: "rain",
        tod: "storm",
        tone: "moody, weather-beaten"
      }
    },
    {
      id: "dawn",
      keys: ["dawn", "sunrise", "morning", "day", "golden", "sun"],
      fx: { tod: "golden", weather: "clear", event: "none", title: "light returns" },
      last_frame:
        "same locked wide: golden hour returns over the floating market",
      world_patch: {
        last_established_action: "sunlight returns to the market",
        tod: "golden",
        weather: "clear",
        tone: "whimsical, adventurous"
      }
    }
  ];

  var UNSAFE = [
    /\b(child|minor|underage|loli|csam)\b/i,
    /\b(nude|porn|explicit sex|nsfw)\b/i,
    /\b(kill (him|her|them)|murder|behead|gore)\b/i,
    /\b(real (person|celebrity)|deepfake)\b/i,
    /\b(nazi|hate speech|racial slur)\b/i,
    /\b(copyrighted|mickey mouse|disney|pokemon|marvel)\b/i
  ];

  function clone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function now() {
    return performance.now();
  }

  function uid(prefix) {
    return prefix + "_" + Math.random().toString(36).slice(2, 8);
  }

  function moderate(text) {
    var t = (text || "").trim();
    if (!t) return { ok: false, reason: "empty" };
    if (t.length > 280) return { ok: false, reason: "too_long" };
    for (var i = 0; i < UNSAFE.length; i++) {
      if (UNSAFE[i].test(t)) return { ok: false, reason: "policy" };
    }
    return { ok: true };
  }

  function matchScene(prompt) {
    var p = (prompt || "").toLowerCase();
    var best = null;
    var score = 0;
    SCENE_BANK.forEach(function (s) {
      var n = 0;
      s.keys.forEach(function (k) {
        if (p.indexOf(k) !== -1) n += 1;
      });
      if (n > score) {
        score = n;
        best = s;
      }
    });
    return best;
  }

  function mergeFx(base, extra) {
    var out = {
      tod: base.tod || "golden",
      weather: base.weather || "clear",
      event: extra && extra.event ? extra.event : "none",
      title: (extra && extra.title) || base.title || "locked camera"
    };
    if (extra) {
      if (extra.tod) out.tod = extra.tod;
      if (extra.weather) out.weather = extra.weather;
    }
    return out;
  }

  function directorCompose(world, userPrompt) {
    var substituted = false;
    var safety = moderate(userPrompt);
    var intent = userPrompt;
    if (!safety.ok && safety.reason === "policy") {
      substituted = true;
      intent = "the camera holds; weather and lanterns shift in place";
    }

    var scene = matchScene(intent);
    var chars = (world.characters || [])
      .map(function (c) { return c.name + " (" + c.visual_desc + ")"; })
      .join("; ");

    var generation_prompt = (
      world.style_prefix + ". SAME LOCKED CAMERA, do not cut, do not change lens. " +
      "Setting: " + world.setting + ". Characters: " + chars +
      ". Last frame: " + world.last_frame_description +
      ". Continuing in this exact shot, " + intent +
      ". Keep " + world.tone + " tone. Under 8 seconds."
    ).slice(0, 900);

    var updated = clone(world);
    updated.last_established_action = intent;
    updated.updated_at = new Date().toISOString();
    var fx = mergeFx(
      { tod: world.tod, weather: world.weather, event: "none", title: "held shot" },
      scene ? scene.fx : { title: "the shot holds · " + intent.slice(0, 42) }
    );
    if (scene && scene.world_patch) {
      Object.keys(scene.world_patch).forEach(function (k) {
        updated[k] = scene.world_patch[k];
      });
      updated.last_frame_description = scene.last_frame;
    }
    updated.tod = fx.tod;
    updated.weather = fx.weather;
    updated.event = fx.event;

    return {
      generation_prompt: generation_prompt,
      updated_world_state: updated,
      substituted: substituted,
      scene: scene,
      fx: fx,
      user_prompt: userPrompt
    };
  }

  function Engine(opts) {
    this.ui = opts.ui;
    this.provider = "mock";
    this.failNext = false;
    this.state = STATES.IDLE_LOOP;
    this.world = clone(DEFAULT_WORLD);
    this.jobs = [];
    this.promptQueue = [];
    this.events = [];
    this.segments = [];
    this.playhead = 0;
    this.bufferAhead = TARGET_BUFFER;
    this.current = null;
    this.lastPromptAt = 0;
    this.running = false;
    this.lastTick = 0;
    this.audioOn = false;
    this.segCounter = 0;
    this.heldFx = clone(IDLE_FX);
  }

  Engine.prototype.log = function (msg) {
    var row = { t: Date.now(), msg: msg };
    this.events.unshift(row);
    if (this.events.length > 80) this.events.pop();
    if (this.ui && this.ui.onLog) this.ui.onLog(row);
  };

  Engine.prototype.setState = function (s) {
    if (this.state === s) return;
    this.state = s;
    this.log("state → " + s);
    if (this.ui && this.ui.onState) this.ui.onState(this.snapshot());
  };

  Engine.prototype.snapshot = function () {
    return {
      state: this.state,
      bufferAhead: this.bufferAhead,
      world: this.world,
      jobs: this.jobs.slice(0, 8),
      queue: this.promptQueue.slice(),
      nowPlaying: this.current ? this.current.title : "",
      current: this.current,
      events: this.events.slice(0, 12)
    };
  };

  Engine.prototype.start = function () {
    var self = this;
    this.running = true;
    this.primeIdleBuffer();
    this.playSegment(this.segments.shift(), true);
    this.lastTick = now();
    var loop = function (t) {
      if (!self.running) return;
      var dt = Math.min(0.08, (t - self.lastTick) / 1000);
      self.lastTick = t;
      self.tick(dt);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    this.log("locked camera live — one plate, no cuts");
  };

  Engine.prototype.makeSegment = function (fields) {
    this.segCounter += 1;
    var fx = fields.fx || clone(this.heldFx);
    return {
      segment_id: "seg_" + String(this.segCounter).padStart(5, "0"),
      source: fields.source,
      prompt_used: fields.prompt_used || "",
      duration_sec: fields.duration || SEGMENT_SEC,
      title: fx.title,
      src: PLATE,
      fx: fx,
      last_frame: fields.last_frame || this.world.last_frame_description,
      conditioning_frame: fields.conditioning_frame || null,
      model_provider: fields.model_provider || this.provider,
      status: "ready",
      loop_candidate: !!fields.loop_candidate,
      created_at: new Date().toISOString()
    };
  };

  Engine.prototype.primeIdleBuffer = function () {
    this.segments = [];
    var depth = 0;
    while (depth < TARGET_BUFFER) {
      var seg = this.makeSegment({
        source: "idle",
        loop_candidate: true,
        duration: SEGMENT_SEC,
        fx: clone(this.heldFx),
        prompt_used: "idle:hold"
      });
      this.segments.push(seg);
      depth += seg.duration_sec;
    }
    this.recomputeBuffer();
  };

  Engine.prototype.recomputeBuffer = function () {
    var remaining = 0;
    if (this.current) remaining += Math.max(0, this.current.duration_sec - this.playhead);
    for (var i = 0; i < this.segments.length; i++) remaining += this.segments[i].duration_sec;
    this.bufferAhead = remaining;
  };

  Engine.prototype.tick = function (dt) {
    if (!this.current) return;
    var stretch = 1;
    if (this.bufferAhead < CRITICAL_BUFFER) stretch = 0.72;
    else if (this.bufferAhead < LOW_BUFFER) stretch = 0.88;

    this.playhead += dt * stretch;
    this.recomputeBuffer();

    if (
      (this.state === STATES.IDLE_LOOP || this.state === STATES.PROMPT_QUEUED) &&
      this.bufferAhead < TARGET_BUFFER &&
      !this.hasJob("user_prompt")
    ) {
      this.refillIdle(false);
    }

    if (this.bufferAhead < CRITICAL_BUFFER && this.state !== STATES.ERROR_RECOVERY) {
      this.extendTail();
      if (this.state !== STATES.BUFFER_LOW && this.state !== STATES.PROMPT_QUEUED &&
          this.state !== STATES.GENERATING_NEW_LOOP && this.state !== STATES.SPLICING) {
        this.setState(STATES.BUFFER_LOW);
      }
    }

    if (this.playhead >= this.current.duration_sec) {
      this.advance();
    }

    if (this.ui && this.ui.onTick) this.ui.onTick(this.snapshot());
  };

  Engine.prototype.advance = function () {
    var next = this.segments.shift();
    if (!next) {
      this.extendTail();
      next = this.segments.shift();
    }
    if (!next) return;

    if (this.state === STATES.SPLICING && next.source === "generated") {
      this.setState(STATES.PLAYING_GENERATED);
    } else if (this.state === STATES.PLAYING_GENERATED && next.source === "idle") {
      this.setState(STATES.IDLE_LOOP);
    } else if (this.state === STATES.GENERATING_NEW_LOOP && next.loop_candidate) {
      this.setState(STATES.IDLE_LOOP);
    } else if (this.state === STATES.BUFFER_LOW && this.bufferAhead > LOW_BUFFER) {
      this.setState(next.source === "generated" ? STATES.PLAYING_GENERATED : STATES.IDLE_LOOP);
    } else if (this.state === STATES.ERROR_RECOVERY && next.loop_candidate) {
      this.setState(STATES.IDLE_LOOP);
    }

    this.playSegment(next, false);

    if (this.state === STATES.PLAYING_GENERATED &&
        this.promptQueue.length === 0 &&
        !this.hasJob("loop") &&
        !this.hasJob("user_prompt")) {
      this.beginReturnToLoop();
    }
  };

  Engine.prototype.playSegment = function (seg) {
    this.current = seg;
    this.playhead = 0;
    this.world.last_frame_description = seg.last_frame || this.world.last_frame_description;
    if (seg.fx) {
      this.heldFx.tod = seg.fx.tod;
      this.heldFx.weather = seg.fx.weather;
      if (seg.source === "idle") this.heldFx.event = "none";
      else this.heldFx.event = seg.fx.event;
      this.heldFx.title = seg.fx.title;
    }
    if (this.ui && this.ui.present) this.ui.present(seg);
    this.recomputeBuffer();
    if (this.ui && this.ui.onState) this.ui.onState(this.snapshot());
  };

  Engine.prototype.refillIdle = function (hold) {
    var fx = clone(this.heldFx);
    fx.event = "none";
    fx.title = this.titleForHold(fx);
    var extra = this.makeSegment({
      source: "idle",
      loop_candidate: true,
      duration: hold ? SEGMENT_SEC * 1.15 : SEGMENT_SEC,
      fx: fx,
      prompt_used: hold ? "extend-tail" : "idle:hold"
    });
    this.segments.push(extra);
    this.recomputeBuffer();
    return extra;
  };

  Engine.prototype.titleForHold = function (fx) {
    if (fx.weather === "rain") return "locked camera · rain holds";
    if (fx.tod === "night") return "locked camera · night holds";
    if (fx.tod === "storm") return "locked camera · storm holds";
    return "locked camera · golden hour";
  };

  Engine.prototype.extendTail = function () {
    var extra = this.refillIdle(true);
    this.log("buffer hold — " + extra.segment_id);
  };

  Engine.prototype.hasJob = function (trigger) {
    return this.jobs.some(function (j) {
      return j.trigger === trigger && (j.status === "pending" || j.status === "running");
    });
  };

  Engine.prototype.submitPrompt = function (raw) {
    var text = (raw || "").trim();
    var safety = moderate(text);
    if (!safety.ok) {
      if (this.ui && this.ui.toast) {
        this.ui.toast(
          safety.reason === "policy"
            ? "That request stays off-air. Try a different direction."
            : "Type a short direction for this shot.",
          "warn"
        );
      }
      this.log("prompt rejected (" + safety.reason + ")");
      return { ok: false, reason: safety.reason };
    }
    var t = now();
    if (t - this.lastPromptAt < RATE_LIMIT_MS) {
      if (this.ui && this.ui.toast) this.ui.toast("Give the director a beat.", "warn");
      return { ok: false, reason: "rate" };
    }
    this.lastPromptAt = t;
    this.promptQueue.push({ text: text, at: Date.now() });
    this.log("queued: " + text);
    this.kickQueue();
    if (this.ui && this.ui.onState) this.ui.onState(this.snapshot());
    return { ok: true };
  };

  Engine.prototype.kickQueue = function () {
    if (this.hasJob("user_prompt")) return;
    if (!this.promptQueue.length) return;
    var item = this.promptQueue.shift();
    if (this.state === STATES.IDLE_LOOP || this.state === STATES.ERROR_RECOVERY ||
        this.state === STATES.BUFFER_LOW) {
      this.setState(STATES.PROMPT_QUEUED);
    }
    this.startJob({
      trigger: "user_prompt",
      user_prompt_raw: item.text,
      priority: "queue"
    });
  };

  Engine.prototype.beginReturnToLoop = function () {
    this.setState(STATES.GENERATING_NEW_LOOP);
    this.startJob({
      trigger: "loop",
      user_prompt_raw: "hold this exact camera; settle the weather into a looping ambient beat",
      priority: "queue"
    });
  };

  Engine.prototype.startJob = function (spec) {
    var self = this;
    var newest = this.segments.length ? this.segments[this.segments.length - 1] : this.current;
    var job = {
      job_id: uid("job"),
      trigger: spec.trigger,
      user_prompt_raw: spec.user_prompt_raw,
      director_prompt_final: "",
      condition_on_segment: newest ? newest.segment_id : null,
      priority: spec.priority,
      status: "pending",
      retries: 0,
      provider: this.provider,
      started_at: Date.now()
    };
    this.jobs.unshift(job);
    if (this.jobs.length > 24) this.jobs.pop();
    this.log("job " + job.job_id + " " + job.trigger);

    var directed = directorCompose(this.world, spec.user_prompt_raw);
    job.director_prompt_final = directed.generation_prompt;
    job.substituted = directed.substituted;
    job.status = "running";
    if (this.ui && this.ui.onState) this.ui.onState(this.snapshot());

    this.generate(job, directed).then(function (seg) {
      job.status = "complete";
      job.latency_ms = Date.now() - job.started_at;
      self.world = directed.updated_world_state;
      if (seg.source === "generated") self.setState(STATES.SPLICING);
      self.segments.push(seg);
      self.recomputeBuffer();
      self.log("in-camera beat " + seg.segment_id);
      if (self.ui && self.ui.onState) self.ui.onState(self.snapshot());
      self.kickQueue();
    }).catch(function (err) {
      job.status = "failed";
      job.error = String(err && err.message ? err.message : err);
      self.log("job failed: " + job.error);
      self.setState(STATES.ERROR_RECOVERY);
      self.extendTail();
      if (self.ui && self.ui.toast) {
        self.ui.toast("Generation missed. Holding this shot.", "warn");
      }
      if (job.trigger === "user_prompt" && job.retries < 1) {
        job.retries += 1;
        self.failNext = false;
        self.startJob({
          trigger: "user_prompt",
          user_prompt_raw: spec.user_prompt_raw,
          priority: "queue"
        });
      } else {
        if (self.ui && self.ui.onState) self.ui.onState(self.snapshot());
        self.kickQueue();
      }
    });
  };

  Engine.prototype.generate = function (job, directed) {
    var self = this;
    var latency = 1800 + Math.random() * 1200;
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        if (self.failNext) {
          self.failNext = false;
          reject(new Error("provider timeout"));
          return;
        }
        var fx;
        if (job.trigger === "loop") {
          fx = {
            tod: directed.fx.tod,
            weather: directed.fx.weather,
            event: "none",
            title: self.titleForHold(directed.fx)
          };
          resolve(self.makeSegment({
            source: "idle",
            loop_candidate: true,
            duration: SEGMENT_SEC,
            fx: fx,
            last_frame: directed.updated_world_state.last_frame_description,
            conditioning_frame: job.condition_on_segment,
            prompt_used: directed.generation_prompt,
            model_provider: self.provider
          }));
        } else {
          fx = directed.fx;
          resolve(self.makeSegment({
            source: "generated",
            loop_candidate: false,
            duration: SEGMENT_SEC,
            fx: fx,
            last_frame: directed.updated_world_state.last_frame_description,
            conditioning_frame: job.condition_on_segment,
            prompt_used: directed.generation_prompt,
            model_provider: self.provider
          }));
        }
      }, latency);
    });
  };

  global.LivestreamEngine = {
    Engine: Engine,
    STATES: STATES,
    DEFAULT_WORLD: DEFAULT_WORLD,
    directorCompose: directorCompose,
    moderate: moderate
  };
})(window);
