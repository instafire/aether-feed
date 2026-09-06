/* Offline demo timeline — used when no engine backend is reachable.
   Same contract as the server: a rolling list of clips always published
   ~18s ahead of the playhead. Clips come from demo/manifest.json, a small
   state graph rendered by the engine's own mock provider + pipeline. */

(function (global) {
  "use strict";

  var TARGET_AHEAD = 18;
  var LOOK_RULES = [
    ["night", /\b(night|stars?|midnight|moon|dark(ness)?|nightfall|dusk|evening)\b/i],
    ["storm", /\b(rain|storm|thunder|downpour|wet|tempest|wind|clouds? roll)\b/i],
    ["golden", /\b(dawn|sunrise|morning|daybreak|clears? up|sun|golden|day|light returns|warm)\b/i]
  ];

  function inferLook(text, current) {
    for (var i = 0; i < LOOK_RULES.length; i++) {
      if (LOOK_RULES[i][1].test(text)) return LOOK_RULES[i][0];
    }
    return current;
  }

  function DemoTimeline(player, manifest, base, ui) {
    this.player = player;
    this.m = manifest;
    this.base = base;
    this.ui = ui;
    this.state = "golden";
    this.pendingPrompt = null;
    this.pendingLook = null;
    this.jobTimer = null;
    this.seq = 0;
    this.jobs = [];
    this.events = [];
    this.world = {
      style_prefix: "cinematic, 35mm film grain, locked wide shot",
      setting: "a floating market town above the clouds",
      characters: [{ name: "the fox-eared courier", visual_desc: "orange fur, blue traveling cloak, brass goggles" }],
      current_camera_state: "locked wide, very slow push-in, never cuts",
      last_established_action: "lanterns sway over the market walkway at golden hour",
      tone: "whimsical, adventurous",
      look: "golden"
    };
    this.engineState = "IDLE_LOOP";
  }

  DemoTimeline.prototype.log = function (msg) {
    this.events.unshift(msg);
    if (this.events.length > 40) this.events.pop();
  };

  DemoTimeline.prototype._clip = function (name, title, source) {
    this.seq += 1;
    return { seq: this.seq, url: this.base + name, duration: this.m.duration_sec, title: title, source: source };
  };

  DemoTimeline.prototype.tick = function () {
    var guard = 0;
    while (this.player.totalAhead() < TARGET_AHEAD && guard < 6) {
      guard += 1;
      if (this.readyTransition) {
        var t = this.readyTransition;
        this.readyTransition = null;
        this.player.enqueue(this._clip(t.file, t.title, "generated"));
        this.state = t.to;
        this.world.look = t.to;
        this.engineState = "PLAYING_GENERATED";
        this.log("spliced " + t.key);
        continue;
      }
      var st = this.m.states[this.state];
      this.player.enqueue(this._clip(st.loop, st.title, "idle"));
    }
    if (this.engineState === "PLAYING_GENERATED") {
      var cur = this.player.currentItem();
      if (cur && cur.source === "idle") this.engineState = "IDLE_LOOP";
    }
  };

  DemoTimeline.prototype.submit = function (prompt, failNext) {
    var self = this;
    if (this.pendingPrompt) return { ok: false, reason: "busy" };
    var to = inferLook(prompt, this.state);
    var key = this.state + ">" + to;
    var file = this.m.transitions[key];
    var job = { trigger: "user_prompt", status: "running", user_prompt_raw: prompt, provider: "mock (offline)" };
    this.jobs.unshift(job);
    this.pendingPrompt = prompt;
    this.engineState = "PROMPT_QUEUED";
    this.log("queued: " + prompt);
    var latency = 2500 + Math.random() * 2500;
    this.jobTimer = setTimeout(function () {
      self.pendingPrompt = null;
      if (failNext) {
        job.status = "failed";
        job.error = "simulated provider timeout";
        self.engineState = "ERROR_RECOVERY";
        self.log("job failed — holding the loop");
        if (self.ui && self.ui.toast) self.ui.toast("Generation missed. Holding the shot.", "warn");
        setTimeout(function () { if (self.engineState === "ERROR_RECOVERY") self.engineState = "IDLE_LOOP"; }, 6000);
        return;
      }
      job.status = "complete";
      job.latency_ms = Math.round(latency);
      self.readyTransition = { file: file, to: to, key: key, title: prompt.slice(0, 48) + " · " + to };
      self.world.last_established_action = prompt;
      self.engineState = "SPLICING";
      self.log("beat ready (" + key + ")");
    }, latency);
    return { ok: true };
  };

  DemoTimeline.prototype.snapshot = function () {
    return {
      state: this.engineState,
      buffer_ahead_sec: this.player.totalAhead(),
      now_playing: (this.player.currentItem() || {}).title || this.m.states[this.state].title,
      world: this.world,
      jobs: this.jobs.slice(0, 8),
      queue: this.pendingPrompt ? [this.pendingPrompt] : [],
      primary_provider: "mock (offline demo)",
      events: this.events.slice(0, 12)
    };
  };

  global.DemoTimeline = DemoTimeline;
  global.inferLook = inferLook;
})(window);
