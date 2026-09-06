# Aether Feed — continuous generative livestream engine

An always-on video feed driven by text-to-video models. An idle loop plays forever. Type a prompt and the engine asks a video model for the next beat **conditioned on the exact last frame on air**, normalizes it, blends the seam, and splices it into a rolling buffer. When the beat ends, a new loop anchored to *its* last frame takes over. The viewer never sees a black frame, a stall, or a clip boundary.

This is the buffered-broadcast design from `generative-livestream-engine-spec.md`: video models are batch generators, so the engine stays ~18 s ahead of the playhead, exactly like a live TV delay.

## How it stays seamless

```
plate ─► idle loop L0 (first frame == last frame == anchor A0)
          L0 L0 L0 L0 …  ◄── fills the buffer, costs nothing
prompt ─► director ─► provider.generate(prompt, image=A0) ─► beat B1
          normalize → crossfade head from L0 tail → fragment → last frame A1
          L0 L0 [B1] ─► hold loop H1 (local palindrome on A1)  ◄── never-blank fallback
                        provider loop L1 anchored on A1 replaces H1 when ready
```

- **Loops are anchored**: every loop starts and ends on its anchor frame, so any number of repeats can play while a provider takes 30–120 s and the beat still joins frame-to-frame.
- **Native continuation preferred**: Veo scene extension, Kling video-extend, Luma generation keyframes are used when the prior clip came from the same provider; image conditioning otherwise.
- **One codec on the wire**: everything passes through ffmpeg (H.264 fMP4 by default) and is appended to a single MSE `SourceBuffer` in `sequence` mode — no `<video>` swaps, no decoder resets.

Verified headless: 95 s continuous playback, one buffered range, beat spliced live (`scripts/browser_check.py`). Frame diff at splice points ≈ 0.8/255 vs. 16/255 between unrelated frames.

## Layout

```
livestream_player/      MSE player (player.js), UI (app.js), offline demo (demo.js, demo/)
generative_livestream/
  app/config.py         env settings
  app/media.py          ffmpeg: normalize, fragment, last frame, crossfade, hold loop, mock renderer
  app/providers/        mock, veo, luma, runway, kling, fal — submit → poll → download
  app/pipeline.py       provider clip → stream-ready segment
  app/timeline.py       wall-clock buffer/state machine, jobs, loop library
  app/director.py       LLM director (OpenAI or Gemini) with deterministic fallback
  app/main.py           FastAPI: playlist, SSE, media, metrics
  scripts/build_demo.py renders the offline demo state graph
  scripts/browser_check.py headless Chromium playback verification
  tests/                pytest (runs the full mock pipeline)
```

## Run

```bash
cd generative_livestream
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add a provider key, or leave mock
uvicorn app.main:app --port 8080
# open http://localhost:8080
```

No keys needed: the **mock provider** is a real ffmpeg image-to-video renderer that honours the same contract (prompt + conditioning frame → MP4), so the whole buffer/splice/MSE path runs for free. Try `night falls`, `a storm rolls in`, `the sun returns`, **Fail next**, **Board**.

### Real models

Set one or more in `.env`, then pick the provider in the UI or `PRIMARY_PROVIDER=`:

| Provider | Key | Conditioning | Native extend | Native loop |
|---|---|---|---|---|
| `veo` | `GEMINI_API_KEY` | inline image | yes (Veo → Veo) | first = last frame |
| `luma` | `LUMA_API_KEY` + `PUBLIC_BASE_URL` | image URL | generation keyframe | `loop: true` |
| `runway` | `RUNWAY_API_KEY` | data URI | — | — |
| `kling` | `KLING_ACCESS_KEY` / `KLING_SECRET_KEY` | base64 | video-extend | — |
| `fal` | `FAL_KEY` (+ `FAL_MODEL`) | data URI | — | — |

`FALLBACK_PROVIDER` runs when the primary errors. `LOOP_PROVIDER` can differ from the beat provider (e.g. Luma for loops, Veo for beats). Set `OPENAI_API_KEY` or reuse `GEMINI_API_KEY` for the LLM director. Sora 2 is intentionally absent (API shutdown 24 Sep 2026).

### API

```
GET  /api/playlist?after=N   live manifest (segments, live edge, codec)
GET  /api/events             SSE: snapshot / segment / job / toast
POST /api/prompt             {"prompt": "...", "provider": "veo"}
POST /api/provider/{name}    switch primary
POST /api/fail-next          simulate a provider failure
GET  /api/metrics            latency, success rate, cost, buffer depth, viewers
GET  /api/media/{loops|segments}/{file}   fragmented clips
GET  /api/frames/{file}      conditioning frames
```

## Offline demo (static hosting)

`livestream_player/` works without a backend: it loads `demo/manifest.json`, a small state graph (3 idle loops + 9 transitions) rendered by the mock provider through the same pipeline. Rebuild with:

```bash
cd generative_livestream && python3 scripts/build_demo.py ../livestream_player/demo
```

## Tests

```bash
cd generative_livestream && pytest -q                # H.264
VIDEO_CODEC=vp9 pytest -q                            # WebM (Chromium builds without H.264)
python3 scripts/browser_check.py http://localhost:8080 45
```

## Spec mapping

FR1/FR7 wall-clock buffer + hold loops · FR2 loop library rotation · FR3–FR6 prompt → director → job → splice → return-to-loop · FR8 world-state style prefix and verbatim character descriptions · FR9 client+server moderation, rate limit · §5.1 MSE · §5.3 normalize · §5.5 native loop / first=last / local palindrome · §5.9 fallback provider, retry, rejection toast · §5.10 `/api/metrics` · §9 crossfade at every splice.

Not in this cut: multi-viewer HLS/CDN (spec §10), RIFE/FILM interpolation, audio bed generation.
