# Continuous generative livestream engine — build specification

## 0. How to use this document

This is written as a build prompt. Hand it to an engineering team, or paste sections into an AI coding assistant (e.g. Claude Code) to scaffold the system. It's organized so you can build it in phases — see Section 12 — you don't need all of this working on day one.

**Assumption made:** this spec targets a single-viewer, personal interactive stream — one person prompting their own always-on generative feed. Section 10 covers what changes for a shared, multi-viewer broadcast (Twitch-style) instead. If that's actually what you want, read Section 10 first, since it changes several components below.

---

## 1. Project summary

Build an engine that plays a continuously running video feed that never stops. By default it shows a seamlessly looping ambient scene. At any time, the user types a prompt, and the engine generates new video that picks up exactly where the current visual left off, so the transition feels like a camera cut within one continuous broadcast rather than a new clip loading. When the prompted scene runs its course, the engine generates a new seamless loop anchored to wherever the story ended, and settles back into "idle." The viewer should never see a black frame, a frozen frame, a spinner, or anything that breaks the sense that this is one unbroken, live feed.

---

## 2. The core illusion — read this before building anything

Today's text-to-video models (Runway, Kling, Luma, Veo, Sora, and the rest) are **batch generators**. You send a prompt, optionally with a conditioning image or clip, and get a finished segment back some seconds to a few minutes later. None of them render frame-by-frame in real time the way a game engine or a camera does. So a literal, zero-latency "type a prompt and watch it appear this instant" stream isn't achievable with these models today, and no architecture will make it so.

What **is** achievable — and what actually reads as "live" to a viewer — is exactly what broadcast television already does: a rolling buffer. The engine always stays some seconds ahead of what the viewer sees, always has a few segments of finished video queued up, and slots new content into that queue the moment it's ready. The viewer never sees a gap; they experience a short delay between submitting a prompt and seeing its effect, the same way live TV has a broadcast delay. That's the design this entire spec is built around, and it's genuinely buildable with commercial APIs today.

If you eventually want true frame-by-frame interactivity — closer to a live game engine than a video stream — that needs a different category of model entirely: real-time "world models." The clearest current example is Google DeepMind's Genie 3, which renders at 24fps and holds visual coherence for a few minutes, but as of this writing it has no public API — it's a closed research demo (Project Genie) limited to Google AI Ultra subscribers in the US. The broader research field (streaming/causal video diffusion — work under names like StreamDiT, Wan-Streamer, Self-Forcing) is actively chasing low-latency frame-by-frame generation, so it's worth revisiting periodically, but it's not something to build a shippable product on top of today. This spec targets the buffered approach, which is.

A useful real-world precedent: *Nothing, Forever*, the AI-generated "Seinfeld" stream that's been running on Twitch on and off since December 2022, proved the always-on generative stream format works and holds an audience — and also got itself suspended for two weeks after a content-filter bug let AI-generated hate speech through. Worth keeping in mind while you build Section 5.8.

---

## 3. Requirements

**Functional**
- **FR1 — Continuous playback.** A viewer connecting at any moment sees smooth, continuously playing video. Never a static or loading screen under normal operation.
- **FR2 — Idle default loop.** With no active prompt, a seamless, perfectly looping ambient scene plays indefinitely. Rotate among a small library of loops so a long idle period doesn't read as an obvious repeat.
- **FR3 — Prompted generation.** The user can submit a free-text prompt at any time. The engine generates new video that continues from the current on-screen moment.
- **FR4 — Seamless transition.** The join between whatever was just playing and the new generated content is visually and motion-continuous — no jump cut unless the prompt clearly asked for one, no flash, no gap.
- **FR5 — Prompt queueing.** A prompt that arrives while another is generating or playing gets queued and chained logically, not dropped or collided.
- **FR6 — Auto-return-to-loop.** After prompted content concludes (fixed duration, a natural pause, or inactivity), the engine generates a new seamless loop anchored to the current visual state and returns to idle.
- **FR7 — Never-blank guarantee.** Under generation failure, API error, or a latency spike, the viewer never sees a black frame or hard stop. The engine extends or re-loops existing buffer instead.
- **FR8 — Visual and narrative consistency.** Style, characters, and setting stay coherent across prompts via a shared "world state" fed into every generation call.
- **FR9 — Prompt safety.** Prompts are filtered for policy-violating content before reaching any video-gen provider.

**Non-functional**
- Never show dead air — this is the single hardest constraint and the one everything else serves.
- Minimize perceptible seams: visual, motion, and (if you add audio) audio.
- Cost stays low while idle — idle playback must not require continuous generation.
- The model integration layer is provider-agnostic — swapping or adding a video-gen backend shouldn't touch the rest of the system.
- Prefer commercial APIs over self-hosted models for v1 — far less operational surface area, at the cost of per-call pricing.

---

## 4. Architecture at a glance

The diagram above shows the core loop: the viewer-facing track (idle loop → rolling buffer → viewer) always has something to play, while the background track (prompt → director → generator) works ahead of it and splices new segments into the buffer the moment they're ready. The generator conditions every call on the last frame of whatever is newest in the buffer — not necessarily what's on-screen right now — since that's the point new content actually has to attach to.

Components, in the order data flows through them:

1. **Frontend player** — continuous playback client
2. **Prompt director** — LLM layer that turns a raw prompt into a full, consistent generation prompt
3. **Generation worker pipeline** — calls the video-gen model, conditioned on the prior segment
4. **Buffer & scheduling manager** — the state machine that hides latency
5. **Loop generation subsystem** — produces and rotates idle loops
6. **Streaming delivery layer** — gets bytes from the pipeline to the player
7. **World-state / consistency engine** — the shared memory that keeps style and characters stable
8. **Moderation & safety layer**
9. **Failure handling & fallbacks**
10. **Observability**

---

## 5. Component specs

### 5.1 Frontend player

The player must chain segments with zero perceptible gap. Three approaches, roughly in order of robustness:

- **MSE (Media Source Extensions), recommended for v1.** A single `<video>` element with a growing `SourceBuffer` you append fMP4/CMAF fragments to as they're ready. No player-level cuts between elements, frame-accurate continuation, full control — the right choice for a single-viewer client talking to your own backend.
- **HLS / LL-HLS.** Treat the generation pipeline as a live encoder producing segments appended to an ever-growing `.m3u8` manifest; play with `hls.js` or native HLS. Heavier than you need for one viewer, but this is the natural choice the moment you go multi-viewer (Section 10), since it's the same tech every live-streaming platform runs on.
- **Double-buffered `<video>` swap.** Preload the next clip in a hidden second `<video>` element and hot-swap at the exact frame boundary. Simplest to prototype, but prone to a visible flicker or duplicated frame if the swap isn't frame-perfect. Fine for a first proof-of-concept, not for the real build.

UI elements: prompt input, a subtle "now playing / composing next scene" indicator (styled to fit the aesthetic, never a generic spinner), non-blocking toast for rejected or failed prompts, standard playback controls.

### 5.2 Prompt director (LLM orchestration layer)

This is the layer that makes the difference between "a slideshow of disconnected AI clips" and something that reads as one continuous world. It receives the raw user prompt plus the current world-state object (5.7) and produces the actual prompt sent to the video model.

Responsibilities:
- Merge user intent with established style, characters, and setting so nothing contradicts what's already on screen.
- Prefer continuity language ("the camera pans to reveal…", "as the door opens…") over hard cuts, unless the user's prompt clearly wants a scene change.
- Reject or gracefully redirect unsafe or policy-violating requests before they ever reach the video-gen API (see 5.8).
- Emit an updated world-state after every segment.

**Starting system prompt** for this layer:

```
You are the creative director for a continuously running, AI-generated
video stream. You do not generate video yourself — you write the exact
prompt that will be sent to a text-to-video model for the next segment.

You will receive:
- WORLD_STATE: established style, setting, characters, current situation.
- LAST_FRAME_DESCRIPTION: a description of the final frame now playing.
- USER_PROMPT: what the user just typed.

Your job:
1. Write one vivid, cinematic prompt (under ~120 words) for the next
   5-10 second segment. It must begin from LAST_FRAME_DESCRIPTION and
   incorporate USER_PROMPT.
2. Prefer continuous camera language over hard cuts, unless the user
   clearly wants a scene change.
3. Reuse established characters' visual descriptions verbatim from
   WORLD_STATE. Never invent a new appearance for an existing character.
4. If USER_PROMPT requests something unsafe, or something a video model
   would reject (real people, copyrighted characters, explicit content),
   do not attempt it. Write a graceful in-world alternative instead and
   flag the substitution.
5. Output an updated WORLD_STATE reflecting what will be true once this
   segment plays.

Respond with strict JSON:
{"generation_prompt": "...", "updated_world_state": {...}, "substituted": false}
```

### 5.3 Generation worker pipeline

- Job queue (Redis + BullMQ, SQS, or similar). A job = "generate the next segment, conditioned on the last frame(s) of segment N, using this prompt."
- The worker calls the video-gen provider, passing the text prompt, a conditioning image or clip (see 5.9 for how to pick this), and any style/reference-image anchors the provider supports.
- **Prefer native video continuation over plain image-conditioning wherever the provider supports it.** A model that extends from the actual last seconds of a clip carries forward motion and camera trajectory, not just a static pose — this alone removes most visible seams. Runway, Kling, Luma, and Veo all support some form of this today (Section 7).
- On completion, normalize the result: consistent resolution, frame rate, and color space regardless of which provider generated it, via ffmpeg. This matters even more if you run a multi-provider fallback chain.
- Apply seam-smoothing (Section 9), then encode into stream-ready fragments and hand off to the buffer manager.

### 5.4 Buffer & scheduling manager

This is the component that actually delivers FR1 and FR7. Core rule: there is always at least N seconds of finished, ready-to-play video ahead of what the viewer is currently watching. Recommend 15–30 seconds as a starting buffer depth.

**State machine:**

| State | What's playing | What's happening in the background | Moves to next state when |
|---|---|---|---|
| `IDLE_LOOP` | Cached idle loop, repeating | Nothing (occasionally rotates to a different cached loop for variety) | A prompt is submitted |
| `PROMPT_QUEUED` | Idle loop continues from buffer | Director composes the full prompt; a generation job starts, conditioned on the frame at the front of the buffer | Generation job completes |
| `SPLICING` | Idle loop, nearing the end of its buffered run | New segment normalized, seam-smoothed, appended to the buffer | Playhead reaches the splice point |
| `PLAYING_GENERATED` | The new AI-generated scene | If another prompt is queued, its generation starts immediately, conditioned on this segment; otherwise, "generate a new loop" starts pre-emptively before this segment ends | Segment ends, or a new prompt arrives |
| `GENERATING_NEW_LOOP` | Tail of the last generated segment (time-stretched if needed) | A new perfect loop is generated, anchored to the current last frame | Loop is ready |
| `BUFFER_LOW` | Existing buffer, possibly time-stretched via frame interpolation | Priority regeneration; alert ops | Buffer restored above threshold |
| `ERROR_RECOVERY` | Falls back to the nearest safe cached loop | Retry the failed generation; notify the user | Recovery succeeds |

**Latency-hiding behavior when a prompt arrives:**
1. Continue playing whatever is already buffered — don't touch it.
2. Kick off generation in the background, conditioned on the *newest* buffered frame, not necessarily what's on-screen this instant.
3. If generation finishes before the buffer runs out, splice it in seamlessly at the boundary.
4. If generation is still running as the buffer nears empty, extend the tail: repeat a couple more seconds of loop, or apply slow-motion time-stretching via frame interpolation (Section 9). Never let the buffer hit zero.

### 5.5 Loop generation subsystem

Three ways to produce a perfect loop, in order of preference:

1. **Native loop support.** Several providers now support this directly — notably Luma's Dream Machine API, which takes a literal `loop: true` parameter and produces a clip built to repeat cleanly. Use this wherever the provider offers it; it removes an entire category of post-processing.
2. **First-frame = last-frame conditioning.** For providers that support first + last frame interpolation (Veo, Luma's keyframes), set the target last frame equal to the first frame. The model generates a clip that returns to exactly where it started.
3. **Algorithmic loop-point-finding.** For anything else: generate a longer clip than you need, then scan frame pairs for the closest visual match (perceptual hash or SSIM), cut between them, and apply a short crossfade at the seam.

Maintain a small library (not just one) of idle loops, and rotate through them during long idle periods — a single loop repeating for an hour is exactly the kind of thing that breaks the "live" illusion for an attentive viewer.

### 5.6 Streaming delivery layer

- **Single viewer:** the simplest robust option is a lightweight server (Node/Express) streaming fMP4 fragments to the client's MSE buffer over a persistent connection, with a WebSocket or SSE channel for state/status signaling.
- **Multi-viewer:** proper live-streaming infrastructure — see Section 10.
- **A strong practical shortcut either way:** render the continuous output into a virtual video feed (a local RTMP or virtual-camera output), then hand it to battle-tested streaming software (OBS, or an ffmpeg pipe into RTMP/SRT) rather than reinventing delivery. This decouples "generate the next segment" from "how live video actually gets delivered," and if you ever do want to go live on Twitch or YouTube, you already have a compatible output.

### 5.7 World-state / consistency engine

A structured object, updated after every segment, that every generation prompt is grounded in. Without this, style and character appearance drift within a handful of prompts. See Section 6 for the schema. Practically: keep a fixed "style prefix" (medium, grain, color grade, tone) that's included in every single prompt verbatim, and use provider reference-image support (Veo and others allow multiple reference images per call) to anchor character appearance whenever a character re-enters frame after being off-screen.

### 5.8 Moderation & safety

- Filter prompts client- and server-side before they reach any video-gen API — both to protect the product and because providers will suspend accounts that repeatedly trigger their own moderation.
- Rate-limit per session.
- Log rejected prompts for tuning the filter, but don't surface the specific reason for a rejection in a way that helps someone iterate toward a bypass.
- If you go multi-viewer, you need this layer regardless — see Section 10 and the *Nothing, Forever* precedent in Section 2.

### 5.9 Failure handling & fallbacks

- Provider timeout or error → retry with backoff; after repeated failure, extend the idle loop and notify the user gently, without breaking the visible stream.
- Provider content-policy rejection → surface this to the user as a request to try a different prompt, not as a broken stream.
- Client network interruption → standard live-streaming reconnect/resume logic.

### 5.10 Observability

Track, at minimum: generation latency per provider, success/failure rate, current buffer depth, cost per segment, active sessions. Alert before the buffer depth gets dangerously low, not after it hits zero.

---

## 6. Data models

**World state** — the shared narrative memory:

```json
{
  "style_prefix": "cinematic, 35mm film grain, warm golden-hour color grade",
  "setting": "a floating market town above the clouds",
  "characters": [
    {"name": "the fox-eared courier", "visual_desc": "orange fur, blue traveling cloak, brass goggles"}
  ],
  "current_camera_state": "slow dolly-in, eye-level",
  "last_established_action": "the courier is descending a rope bridge toward a market stall",
  "tone": "whimsical, adventurous",
  "updated_at": "2026-09-05T14:32:00Z"
}
```

**Segment** — an entry in the buffer/manifest:

```json
{
  "segment_id": "seg_00042",
  "source": "generated",
  "prompt_used": "...",
  "duration_sec": 6.2,
  "conditioning_frame": "seg_00041_lastframe.jpg",
  "model_provider": "veo-3.1",
  "status": "ready",
  "loop_candidate": false,
  "created_at": "..."
}
```

**Generation job:**

```json
{
  "job_id": "job_1234",
  "trigger": "user_prompt",
  "user_prompt_raw": "a dragon lands on the bridge",
  "director_prompt_final": "...",
  "condition_on_segment": "seg_00041",
  "priority": "interrupt | queue",
  "status": "pending | running | complete | failed",
  "retries": 0
}
```

---

## 7. Model integration notes (as of September 2026)

This space ships new model versions monthly — treat the specifics below as a starting point and re-check each provider's docs before locking in an integration.

- **Google Veo (3 / 3.1, via the Gemini API, paid preview).** The closest conceptual match to what you're building: its **Scene extension** feature explicitly generates the next clip conditioned on the final second of the previous one — literally the mechanism this spec is built around. Also supports first-and-last-frame interpolation (handy for loops — set the last frame equal to the first) and up to three reference images for holding character and style constant. Documented request latency runs roughly 11 seconds to several minutes at peak load, which is a reasonable number to design your buffer depth around.
- **Luma Dream Machine (Ray2, via API).** The standout for the idle-loop subsystem specifically — its generation call takes a literal `loop` boolean, so the provider handles making a clip cleanly repeatable without you building loop-point detection for the common case. Also supports explicit start/end keyframes and a separate extend action.
- **Kling AI.** Strong extend-chaining: a base 5s or 10s clip can be extended roughly 4–5 seconds at a time (either "Auto-Extend," driven by the model's own read of the footage, or "Customized Extend," driven by your prompt) up to a 3-minute total — though once you start extending, the model and mode lock to match the source clip. Also supports image-to-video with explicit start/end reference frames.
- **Runway (Gen-4 / Gen-4.5 / Gen-4 Aleph).** Task-based async API supporting text-to-video, image-to-video, and true video-to-video (Aleph, which can restyle or transform an existing clip while preserving motion — useful for style-locking a segment). Typical clip length is 5–8 seconds, with a dedicated extend endpoint that continues from a specified frame of a prior generation.
- **OpenAI Sora 2.** Capable — real-world physics, native audio, an extend feature, and a "Remix" mode for targeted edits to an existing clip. **Time-sensitive flag: OpenAI has scheduled the Sora 2 API for shutdown on September 24, 2026** — about three weeks out from when this document was written. Don't build a hard dependency on it without a fallback provider already wired in.

**If you later want genuine real-time interactivity** rather than the buffered approach: Genie 3 (Google DeepMind) is the clearest current example of a real-time, 24fps generative video system, but it has no public API today — it's a closed consumer demo. Not viable to build a product on top of yet; worth watching, not worth blocking on.

Given the pace of change here, build the generation-worker layer (5.3) as a thin, swappable adapter per provider from day one — you will likely end up running two providers in parallel (a primary plus a fallback) for reliability alone, independent of any single provider's roadmap.

---

## 8. Suggested tech stack

| Layer | Recommended | Alternatives |
|---|---|---|
| Orchestration backend | Node.js/TypeScript (Fastify) or Python (FastAPI) | Go, for high-throughput multi-viewer |
| Job queue | Redis + BullMQ | AWS SQS, RabbitMQ |
| Video-gen access | Provider APIs — one primary, one fallback | Self-hosted OSS models (LTX-Video, CogVideoX, Mochi) on rented GPUs |
| Director/LLM layer | Any capable LLM via API | — |
| Media processing | FFmpeg | GStreamer |
| Seam smoothing | RIFE or FILM (open-source frame interpolation) | — |
| Delivery (single viewer) | MSE-based player over a persistent connection | HLS.js against a lightweight local packager |
| Delivery (multi-viewer) | RTMP/SRT out to a managed live platform, or self-hosted OvenMediaEngine | nginx-rtmp |
| Storage | S3-compatible object storage | — |
| State/session | PostgreSQL + Redis | — |
| Frontend | React + Tailwind + native MSE / hls.js | — |

---

## 9. Making the seams invisible

- Always condition the next generation on the exact last frame (or last 1–2 seconds) of the previous segment. Prefer a provider's native video-extend/continuation feature over single-frame conditioning wherever available — it carries forward velocity and camera motion, not just a static pose.
- Apply a short (4–10 frame) crossfade at every splice point even when conditioning is good — cheap insurance against small lighting or grain mismatches.
- If a visible jump in motion shows up anyway, use frame interpolation (RIFE/FILM) to generate 2–6 synthetic in-between frames at the seam.
- Lock generation parameters (seed where supported, model version, resolution, frame rate, color profile) across a session wherever the provider allows it, to reduce stylistic drift call to call.
- If you add audio: treat it as its own continuity problem. A continuous ambient music bed underneath the generated video is far more forgiving than trying to seam-match per-segment generated audio — reserve provider-native synced audio (Veo, Sora) for segments where it clearly adds value, and crossfade at the same points you crossfade video.

---

## 10. If you want a shared, multi-viewer broadcast instead

This spec assumes one person steering their own stream. If you actually want many simultaneous viewers watching (and possibly prompting) the same stream — a Twitch-style format — the following change:

- **Delivery becomes real live-streaming infrastructure.** One encoder, many viewers: proper HLS/LL-HLS origin plus CDN (a managed platform, or self-hosted with OvenMediaEngine/nginx-rtmp), not a per-viewer MSE connection.
- **The prompt queue needs moderation and arbitration**, since many people may prompt at once — options include a cooldown per prompt, majority voting, or a moderator approval step. This is exactly the failure mode that got *Nothing, Forever* suspended (Section 2); budget real effort here, not a token filter.
- **Cost changes shape, not necessarily total**: one continuous generation pipeline serving N viewers, rather than N independent pipelines — cheaper per-viewer at scale, but you're now generating continuously regardless of whether anyone is actively prompting, so idle-loop cost control (Section 11) matters even more.

---

## 11. Cost control

- Idle playback should cost close to nothing — loop cached clips, don't regenerate them. Generation cost is only incurred for the initial loop library and for each user-prompted segment.
- Segment length is a real trade-off: shorter segments react faster and cost less per call but create more seams and more API round-trips; longer segments are smoother but slower to respond to a new prompt and require a deeper buffer. 5–8 seconds per segment is a reasonable starting point, matching what most providers treat as a single native generation.
- Resolution/quality is another lever — most providers charge roughly linearly with resolution and duration, so it's worth generating at the lowest resolution that still looks good on your target display and upscaling client-side if needed.

---

## 12. Suggested build order

1. **Prove the seam.** One idle loop, one manual prompt, one generated continuation, back to loop. Simple double-buffered `<video>` swap is fine here. No queueing, no buffer manager — the goal is only to confirm the conditioning-on-last-frame approach actually looks continuous with your chosen model.
2. **Add the real buffer.** Build the state machine (5.4), proper MSE-based playback, and the never-blank guarantee. This is the phase that makes it feel "live."
3. **Add the director and world-state layer.** Narrative/style consistency across many consecutive prompts, multi-provider support, the loop library and rotation.
4. **Scale out.** Multi-viewer broadcast support (Section 10), moderation at scale, full observability.

---

## 13. Definition of done

- [ ] The viewer never sees a black frame, an unintentionally frozen frame, or a player error during normal operation.
- [ ] The idle loop plays with no detectable seam for at least 30 minutes of continuous looping (loop rotation included).
- [ ] From prompt submission to the new content appearing, latency is bounded, and the buffer never silently underruns.
- [ ] New segments continue visually and motion-wise from the exact last frame shown — no unintended jump cuts.
- [ ] A single generation failure does not visibly disrupt playback.
- [ ] Style and character descriptions hold across at least 10 consecutive prompted segments.
- [ ] The full loop — prompt in, world-state update, generation job, normalization, splice, playback — is observable end-to-end in logs or a dashboard.

---

## 14. Decisions still yours to make

This spec makes reasonable defaults where the brief didn't specify. Worth deciding explicitly before you build:

- **Single-viewer vs. multi-viewer** — assumed single-viewer throughout; Section 10 covers the alternative.
- **The idle loop's genre/aesthetic** — nature scene, abstract art, a character in a room, a cityscape — entirely open; whatever you pick becomes the seed of your world-state.
- **Segment length** — 5–8s is the suggested default; shorter reacts faster, longer looks smoother.
- **Primary + fallback model provider** — pick based on whichever you already have API access to; strongly recommend wiring up two from the start given how fast this space moves (Section 7).
- **Self-host vs. API** — API-based is the recommended starting point; self-hosting open models cuts long-run cost but adds real infrastructure weight (GPU hosting, model serving) that's worth deferring past an MVP.
- **How aggressively prompts chain into one continuous story** vs. feeling more like changing the channel — a UX and world-state design choice, not just a technical one.

---

## 15. References

- Veo on the Gemini API (scene extension, keyframes, reference images): https://ai.google.dev/gemini-api/docs/veo
- Veo 3.1 announcement: https://developers.googleblog.com/introducing-veo-3-1-and-new-creative-capabilities-in-the-gemini-api/
- Luma Dream Machine API (loop, keyframes, extend): https://docs.lumalabs.ai/docs/video-generation
- Kling AI video extension guide: https://kling.ai/quickstart/ai-video-extension
- Runway API guide: https://docs.dev.runwayml.com/guides/using-the-api/
- Sora 2 / Videos API (including the September 2026 deprecation notice): https://developers.openai.com/api/docs/guides/video-generation
- Genie 3 (real-time world models, for context on the non-buffered alternative): https://deepmind.google/models/genie/
