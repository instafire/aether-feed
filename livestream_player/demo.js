/* Offline demo timeline — used when no engine backend is reachable.
   Same contract as the server: a rolling list of clips always published
   ~18s ahead of the playhead, plus a small audience engine (chat votes,
   gift spectacles) mirroring app/audience.py. */

(function (global) {
  "use strict";

  var TARGET_AHEAD = 18;
  var LOOK_RULES = [
    ["night", /\b(night|stars?|midnight|moon|dark(ness)?|nightfall|dusk|evening)\b/i],
    ["storm", /\b(rain|storm|thunder|downpour|wet|tempest|wind|clouds? roll)\b/i],
    ["golden", /\b(dawn|sunrise|morning|daybreak|clears? up|sun|golden|day|light returns|warm|fireworks?|lantern|petal|aurora|comet|dragon|birds?)\b/i]
  ];
  var DIRECTION_HINT = /\b(night|day|dawn|sunrise|sunset|rain|storm|snow|fog|wind|fire|dragon|bird|boat|lantern|sky|cloud|moon|star|sun|light|dark|golden|aurora|comet|firework|petal|falls?|rolls? in)\b/i;
  var STOP = /^(a|an|the|and|or|of|to|in|on|at|for|with|is|are|it|this|that|i|you|we|so|very|just|like|please|pls|make|do|lol|wow|ok|hi|hey)$/;
  var GIFT_MAP = {
    rose: "a drift of rose petals crosses the frame on the wind",
    heart: "paper lanterns rise gently into the sky",
    tiktok: "a ripple of light runs along every lantern in the market",
    galaxy: "an aurora unfurls across the sky above the market",
    lion: "a great amber dragon glides across the bridge",
    universe: "the whole sky blooms into a slow supernova of light",
    cheer: "fireworks bloom silently over the cloud sea",
    superchat: "a comet arcs slowly over the market"
  };

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
    this.queue = [];      // {prompt, trigger, priority, user}
    this.running = null;
    this.readyTransition = null;
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
    // audience
    this.recent = [];
    this.chatTimes = [];
    this.giftLog = [];
    this.gifters = {};
    this.likes = 0; this.follows = 0; this.shares = 0;
    this.candidates = [];
    this.windowStart = performance.now();
    this.windowSec = 20;
    this.lastGiftDirection = 0;
    this.lastVote = null;
    this.alog = [];
  }

  DemoTimeline.prototype.log = function (msg) { this.events.unshift(msg); if (this.events.length > 40) this.events.pop(); };

  DemoTimeline.prototype._clip = function (name, title, source) {
    this.seq += 1;
    return { seq: this.seq, url: this.base + name, duration: this.m.duration_sec, title: title, source: source };
  };

  DemoTimeline.prototype.tick = function () {
    var vote = this._closeWindow();
    if (vote) this._enqueue(vote);
    if (!this.running && this.queue.length) this._start(this.queue.shift());
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

  DemoTimeline.prototype._enqueue = function (item) {
    if (item.priority === "interrupt") this.queue.unshift(item); else this.queue.push(item);
    this.engineState = "PROMPT_QUEUED";
    this.log(item.trigger + " from " + item.user + ": " + item.prompt.slice(0, 60));
  };

  DemoTimeline.prototype._start = function (item) {
    var self = this;
    var to = inferLook(item.prompt, this.state);
    var key = this.state + ">" + to;
    var file = this.m.transitions[key];
    var job = { trigger: item.trigger, status: "running", user_prompt_raw: item.prompt, provider: "mock (offline)", requested_by: item.user };
    this.jobs.unshift(job);
    this.running = job;
    this.engineState = "PROMPT_QUEUED";
    var failNext = item.failNext;
    var latency = 2500 + Math.random() * 2500;
    setTimeout(function () {
      self.running = null;
      if (failNext) {
        job.status = "failed"; job.error = "simulated provider timeout";
        self.engineState = "ERROR_RECOVERY";
        self.log("job failed — holding the loop");
        if (self.ui && self.ui.toast) self.ui.toast("Generation missed. Holding the shot.", "warn");
        setTimeout(function () { if (self.engineState === "ERROR_RECOVERY") self.engineState = "IDLE_LOOP"; }, 6000);
        return;
      }
      job.status = "complete"; job.latency_ms = Math.round(latency);
      var prefix = item.trigger === "gift" ? "🎁 " + item.user + ": " : (item.trigger === "chat_vote" ? "chat: " : "");
      self.readyTransition = { file: file, to: to, key: key, title: prefix + item.prompt.slice(0, 44) + " · " + to };
      self.world.last_established_action = item.prompt;
      self.engineState = "SPLICING";
      self.log("beat ready (" + key + ")");
    }, latency);
  };

  DemoTimeline.prototype.submit = function (prompt, failNext) {
    if (this.queue.length > 3) return { ok: false, reason: "busy" };
    this._enqueue({ prompt: prompt, trigger: "user_prompt", priority: "queue", user: "you", failNext: failNext });
    return { ok: true };
  };

  // ------------------------------------------------------------- audience
  DemoTimeline.prototype.ingest = function (ev) {
    ev.ts = ev.ts || Date.now() / 1000;
    this.recent.push(ev); if (this.recent.length > 200) this.recent.shift();
    if (this.ui && this.ui.onAudienceEvent) this.ui.onAudienceEvent(ev);
    if (ev.type === "chat") {
      this.chatTimes.push(performance.now());
      var text = ev.text.trim();
      var cmd = /^!scene\b/i.test(text);
      if (cmd) text = text.replace(/^!scene\s*:?\s*/i, "");
      if (!text || (!cmd && !DIRECTION_HINT.test(text))) return;
      var hints = (text.toLowerCase().match(DIRECTION_HINT.source ? new RegExp(DIRECTION_HINT.source, "gi") : DIRECTION_HINT) || []).map(function (w) { return w.toLowerCase(); });
      var key = hints.length ? hints.sort().join(" ") : text.toLowerCase().split(/[^a-z']+/).filter(function (w) { return w && !STOP.test(w); }).sort().join(" ");
      if (key) this.candidates.push({ key: key, text: text.slice(0, 140), user: ev.user });
    } else if (ev.type === "gift") {
      var total = Math.max(1, ev.gift_value || 1) * Math.max(1, ev.count || 1);
      this.giftLog.push([performance.now(), total]);
      this.gifters[ev.user] = (this.gifters[ev.user] || 0) + total;
      var tier = total >= 1000 ? "large" : (total >= 100 ? "medium" : "small");
      var name = (ev.gift_name || "").toLowerCase();
      var spectacle = null;
      Object.keys(GIFT_MAP).forEach(function (k) { if (!spectacle && name.indexOf(k) !== -1) spectacle = GIFT_MAP[k]; });
      if (!spectacle) spectacle = { small: "a single lantern flares brighter for a moment", medium: "a flock of glowing paper birds sweeps across the sky", large: "the sky ignites with slow golden fireworks over the whole market" }[tier];
      var now = performance.now();
      if (tier === "small" && now - this.lastGiftDirection < 12000) { this.alog.unshift(ev.user + " sent " + ev.gift_name + " (cooldown)"); return; }
      this.lastGiftDirection = now;
      this.alog.unshift(ev.user + " sent " + ev.gift_name + " → " + tier + " spectacle");
      this._enqueue({ prompt: spectacle + " — a thank-you to " + ev.user, trigger: "gift", priority: tier === "large" ? "interrupt" : "queue", user: ev.user });
    } else if (ev.type === "like") this.likes += ev.count || 1;
    else if (ev.type === "follow") this.follows += 1;
    else if (ev.type === "share") this.shares += 1;
  };

  DemoTimeline.prototype._cluster = function () {
    var clusters = [];
    this.candidates.forEach(function (c) {
      var ws = c.key.split(" ");
      var home = null;
      for (var i = 0; i < clusters.length && !home; i++) {
        for (var j = 0; j < ws.length; j++) if (clusters[i].words.indexOf(ws[j]) !== -1) { home = clusters[i]; break; }
      }
      if (!home) { home = { words: [], items: [] }; clusters.push(home); }
      ws.forEach(function (w) { if (home.words.indexOf(w) === -1) home.words.push(w); });
      home.items.push(c);
    });
    clusters.sort(function (a, b) { return b.items.length - a.items.length; });
    return clusters;
  };

  DemoTimeline.prototype._closeWindow = function () {
    if ((performance.now() - this.windowStart) / 1000 < this.windowSec) return null;
    this.windowStart = performance.now();
    if (!this.candidates.length) return null;
    var top = this._cluster()[0];
    this.candidates = [];
    var counts = {};
    top.items.forEach(function (c) { counts[c.text] = (counts[c.text] || 0) + 1; });
    var best = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; })[0];
    var voters = top.items.map(function (c) { return c.user; });
    this.lastVote = { prompt: best, votes: top.items.length };
    this.alog.unshift("chat vote: '" + best + "' (" + top.items.length + ")");
    return { prompt: best, trigger: "chat_vote", priority: "queue", user: voters[0] || "chat" };
  };

  DemoTimeline.prototype.audience = function () {
    var now = performance.now();
    var cpm = this.chatTimes.filter(function (t) { return now - t < 60000; }).length;
    var coins = this.giftLog.filter(function (g) { return now - g[0] < 60000; }).reduce(function (a, g) { return a + g[1]; }, 0);
    var hype = Math.min(1, 0.6 * Math.min(1, cpm / 60) + 0.6 * Math.min(1, coins / 1000));
    var leader = null;
    if (this.candidates.length) { var top = this._cluster()[0]; leader = { prompt: top.items[0].text, votes: top.items.length }; }
    var gifters = Object.keys(this.gifters).map(function (u) { return { user: u, coins: this.gifters[u] }; }, this).sort(function (a, b) { return b.coins - a.coins; }).slice(0, 3);
    return {
      chat_per_min: cpm, coins_per_min: coins, hype: hype,
      energy: hype > 0.6 ? "high" : (hype > 0.25 ? "medium" : "calm"),
      likes: this.likes, follows: this.follows, shares: this.shares,
      top_gifters: gifters, trending: [],
      vote_window_sec: Math.max(0, this.windowSec - (now - this.windowStart) / 1000),
      vote_leader: leader, vote_candidates: this.candidates.length, last_vote: this.lastVote,
      sources: { simulator: this.simOn ? "on" : "off" }, log: this.alog.slice(0, 10)
    };
  };

  var SIM_USERS = ["mira", "kenji", "ada", "lou", "priya", "theo", "sol", "nia", "rafa", "june"];
  var SIM_CHAT = ["this is so pretty", "night mode pls", "!scene a storm rolls in", "make it night", "lol the lanterns", "!scene the sun comes back", "can we see rain", "so calm", "!scene night falls over the market", "wow", "storm storm storm", "hi from brazil", "!scene fireworks over the market", "morning light please"];
  var SIM_GIFTS = [["Rose", 1, 40], ["Heart", 1, 30], ["TikTok", 1, 20], ["Galaxy", 1000, 4], ["Lion", 29999, 1], ["cheer", 100, 3], ["superchat", 499, 2]];

  DemoTimeline.prototype.setSimulator = function (on) {
    var self = this;
    this.simOn = on;
    if (this.simTimer) { clearTimeout(this.simTimer); this.simTimer = null; }
    if (!on) return;
    var lastGift = performance.now();
    function step() {
      if (!self.simOn) return;
      var user = SIM_USERS[Math.floor(Math.random() * SIM_USERS.length)];
      if (performance.now() - lastGift > 25000) {
        lastGift = performance.now();
        var total = SIM_GIFTS.reduce(function (a, g) { return a + g[2]; }, 0), r = Math.random() * total, pick = SIM_GIFTS[0];
        for (var i = 0; i < SIM_GIFTS.length; i++) { r -= SIM_GIFTS[i][2]; if (r <= 0) { pick = SIM_GIFTS[i]; break; } }
        self.ingest({ platform: "sim", type: "gift", user: user, gift_name: pick[0], gift_value: pick[1], count: 1 });
      } else {
        var k = Math.random();
        if (k < 0.08) self.ingest({ platform: "sim", type: "like", user: user, count: 1 + Math.floor(Math.random() * 15) });
        else if (k < 0.11) self.ingest({ platform: "sim", type: "follow", user: user });
        else self.ingest({ platform: "sim", type: "chat", user: user, text: SIM_CHAT[Math.floor(Math.random() * SIM_CHAT.length)] });
      }
      self.simTimer = setTimeout(step, 500 + Math.random() * 3500);
    }
    step();
  };

  DemoTimeline.prototype.snapshot = function () {
    return {
      state: this.engineState,
      buffer_ahead_sec: this.player.totalAhead(),
      now_playing: (this.player.currentItem() || {}).title || this.m.states[this.state].title,
      world: this.world,
      jobs: this.jobs.slice(0, 8),
      queue: this.queue.map(function (q) { return q.prompt; }),
      primary_provider: "mock (offline demo)",
      events: this.events.slice(0, 12),
      audience: this.audience(),
      format: { format: this.m.format || "landscape", width: this.m.width, height: this.m.height }
    };
  };

  global.DemoTimeline = DemoTimeline;
  global.inferLook = inferLook;
})(window);
