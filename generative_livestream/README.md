# Aether Feed — continuous generative livestream engine

A single-viewer, always-on generative video feed. An idle loop plays forever. Type a prompt and the engine generates the next scene **conditioned on the newest buffered frame**, then splices it in. When the scene ends, it anchors a new idle loop. The viewer never sees a black frame.

This is the buffered-broadcast design from the spec: today's video models are batch generators, so the engine stays ~20s ahead of the playhead — the same trick live television uses.

## What you get

| Layer | What it does |
|---|---|
| **Player** (`livestream_player/`) | Never-blank cinematic stage, Ken Burns idle loops, 420ms crossfades, prompt bar, world-state board |
| **Buffer manager** | Spec §5.4 state machine: `IDLE_LOOP → PROMPT_QUEUED → SPLICING → PLAYING_GENERATED → GENERATING_NEW_LOOP`, plus `BUFFER_LOW` / `ERROR_RECOVERY` |
| **Director** | Spec §5.2 system prompt. Merges user intent with world-state; refuses unsafe requests in-world |
| **World state** | Style prefix, setting, characters (verbatim visual_desc), camera, last frame |
| **Providers** | Thin adapters: `mock`, `veo` (scene extension), `luma` (`loop: true`), `runway`, `kling`. Missing keys fall back to mock |
| **Safety** | Client + server filter, rate limit, no bypass-hinting error copy |
| **Delivery** | FastAPI + SSE for status; static player. Swap the presenter for MSE/fMP4 when you have real clips |

Default world: a floating market town above the clouds, golden-hour 35mm, the fox-eared courier in a blue cloak.

## Run the player (no backend)

Open `livestream_player/index.html` as a static site (the exported artifact), or:

```bash
python3 -m http.server 8080 --directory livestream_player
```

Mock generation uses the still library so the feed is live without API keys. Try:

- `a dragon lands on the bridge`
- `night falls over the market`
- `a storm rolls in`
- **Fail next** — forces a provider timeout; the loop holds
- **Board** — world state, jobs, log

## Run the engine

```bash
cd generative_livestream
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080

```
POST /api/prompt          { "prompt": "a dragon lands", "provider": "mock" }
GET  /api/state
GET  /api/stream          SSE snapshots
POST /api/fail-next
```

Wire a real model by setting `GEMINI_API_KEY` / `LUMA_API_KEY` / `RUNWAY_API_KEY` and choosing that provider. Adapters prefer native extend / loop flags (spec §7, §9).

## Tests

```bash
cd generative_livestream
pip install pytest
pytest -q
```

Includes an 8-minute simulated playback that asserts the buffer never hits zero.

## Spec mapping

- FR1 / FR7 never-blank — idle refill + time-stretch + tail extend
- FR2 idle library rotation (3 cached loops, no generation while idle)
- FR3–FR6 prompt → director → job → splice → return-to-loop
- FR8 world-state style prefix + verbatim character descriptions
- FR9 moderation before any provider call
- §12 phase 1–3: seam, buffer, director. Phase 4 (multi-viewer HLS) is not in this cut — see spec §10

Sora 2 is intentionally not a hard dependency (API shutdown 24 Sep 2026).
