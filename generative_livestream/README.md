# Aether Feed — interactive generative livestream engine

An always-on video feed driven by text-to-video models and steered by its **audience**. An idle loop plays forever. Prompts — typed by you, voted by chat, or triggered by gifts — ask a video model for the next beat **conditioned on the exact last frame on air**; the engine normalizes it, blends the seam, and splices it into a rolling buffer. When the beat ends, a new loop anchored to *its* last frame takes over. No black frames, no stalls, no clip boundaries.

Output can run **landscape 16:9** (YouTube / Twitch), **portrait 9:16** (TikTok LIVE / Reels / Shorts) or **square 1:1**, at 540p / 720p / 1080p, switchable live.

## What's inside

| | |
|---|---|
| **MSE player** | One `<video>`, one `SourceBuffer` in sequence mode; clips stitched frame-accurately |
| **Providers** | Veo 3.1 (image→video, scene extension, first=last loop), Luma Ray 2 (`loop:true`, keyframes), Runway Gen-4, Kling (video-extend), fal.ai — full submit → poll → download |
| **Mock provider** | A real ffmpeg image-to-video renderer on the same contract, so everything runs with zero API cost |
| **Pipeline** | normalize → crossfade head from prior tail → fragment → last frame |
| **Timeline** | wall-clock buffer (~18 s ahead), splice at loop boundaries, local hold loop so the tail never freezes |
| **Audience engine** | chat voting windows, gift tiers → spectacles, hype meter → director energy |
| **Sources** | TikTok LIVE (via `TikTokLive`), Twitch IRC, YouTube Live chat / Super Chat, generic webhook, simulator |
| **Director** | OpenAI or Gemini with audience context; deterministic fallback |
| **Formats** | landscape / portrait / square × 540 / 720 / 1080, live switch = new session |

## How it stays seamless

```
plate ─► idle loop L0 (first == last frame == anchor A0)   L0 L0 L0 …
prompt/gift/vote ─► director ─► provider(prompt, image=A0) ─► beat B1
     normalize → crossfade from L0 tail → fragment → last frame A1
     L0 L0 [B1] ─► hold loop H1 on A1 (local, instant) ─► provider loop L1 on A1
```

Loops are anchored, so any number of repeats can play while a provider takes 30–120 s and the join is still frame-continuous. Verified headless (Chromium): 95 s continuous playback, single buffered range; splice frame diff ≈ 0.8/255.

## Audience → stream

| Signal | Behaviour |
|---|---|
| `!scene night falls` or any direction-like message | joins the current vote window (default 20 s); similar phrasings cluster; winner is generated once per window |
| Gift < 100 coins | small spectacle (petals, lantern flare), 12 s cooldown |
| Gift 100–999 | medium spectacle (paper birds, aurora), queued |
| Gift ≥ 1000 | large spectacle (dragon, fireworks), **interrupts** the queue |
| Chat rate + coins/min | hype → director energy hint (calm / medium / high) |
| Named gifts | `GIFT_MAP_JSON` maps gift names to in-world phrases (Rose → petals, Galaxy → aurora, Lion → dragon…) |

Everything passes moderation first. Usernames are acknowledged in the prompt but never rendered as text.

## Run

```bash
cd generative_livestream
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # add provider keys / platform ids, or leave mock
uvicorn app.main:app --port 8080
# open http://localhost:8080
```

Try: type `night falls`, click **Simulate audience**, send a 🦁 **Lion**, switch **Output** to Portrait 9:16.

### Connect a platform

```env
TIKTOK_UNIQUE_ID=yourhandle          # pip install TikTokLive (community webcast client)
TWITCH_CHANNEL=yourchannel           # anonymous IRC, bits + subs + raids
YOUTUBE_API_KEY=... YOUTUBE_VIDEO_ID=...   # live chat + Super Chat
```
Or relay anything: `POST /api/audience/event {"platform":"kick","type":"gift","user":"mira","gift_name":"Rose","gift_value":1}`

### Real video models

| Provider | Key | Conditioning | Native extend | Native loop |
|---|---|---|---|---|
| `veo` | `GEMINI_API_KEY` | inline image | yes | first = last frame |
| `luma` | `LUMA_API_KEY` + `PUBLIC_BASE_URL` | image URL | generation keyframe | `loop: true` |
| `runway` | `RUNWAY_API_KEY` | data URI | — | — |
| `kling` | `KLING_ACCESS_KEY` / `KLING_SECRET_KEY` | base64 | video-extend | — |
| `fal` | `FAL_KEY` (+ `FAL_MODEL`) | data URI | — | — |

Aspect ratio follows the selected output format on every provider.

### API

```
GET  /api/playlist?after=N      live manifest (segments, live edge, codec, session, format)
GET  /api/events                SSE: snapshot / segment / job / session / audience_event / direction / toast
POST /api/prompt                {"prompt": "...", "provider": "veo"}
GET|POST /api/format            {"format": "portrait", "size": "720"}  → new session
GET  /api/audience              hype, votes, gifters, trending, sources
POST /api/audience/event        webhook ingest
POST /api/audience/simulate/on  synthetic chat + gifts
POST /api/provider/{name}  ·  POST /api/fail-next  ·  GET /api/metrics
GET  /api/media/{loops|segments}/{file}  ·  GET /api/frames/{file}
```

## Offline demo (static hosting)

`livestream_player/` works without a backend: it loads a small state graph (3 loops + 9 frame-continuous transitions) rendered by the mock provider — `demo/` (landscape H.264), `demo_vp9/`, `demo_portrait/` — and runs the same audience logic in JS. Rebuild:

```bash
cd generative_livestream
python3 scripts/build_demo.py ../livestream_player/demo
STREAM_FORMAT=portrait python3 scripts/build_demo.py ../livestream_player/demo_portrait
VIDEO_CODEC=vp9 python3 scripts/build_demo.py ../livestream_player/demo_vp9
```

## Tests

```bash
cd generative_livestream && pytest -q                 # 14 tests, full mock pipeline incl. gift interrupt + portrait restart
python3 scripts/browser_check.py http://localhost:8080 45
python3 scripts/browser_check_formats.py http://localhost:8080
```

## Not in this cut

Multi-viewer HLS/CDN egress (spec §10 — the timeline is already single-encoder; add an RTMP/HLS packager on `/api/media`), RIFE frame interpolation, generated audio bed, TikTok official gift API (none exists; the community client or a relay is required).
