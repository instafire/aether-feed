# Aether Feed

A single-viewer, always-on generative video feed. An idle loop plays forever on **one locked camera**. Type a prompt and the engine generates the next beat **conditioned on the current shot**, then morphs weather, light, and in-frame events in place. When the beat ends, it settles back into idle. The viewer never sees a black frame or a hard cut to a different clip.

This is the buffered-broadcast design from the spec: today's video models are batch generators, so the engine stays ~20s ahead of the playhead.

## Layout

```
livestream_player/          # never-blank cinematic player (open index.html)
generative_livestream/      # FastAPI buffer, director, providers, tests
generative-livestream-engine-spec.md
```

## Player (no backend)

```bash
python3 -m http.server 8080 --directory livestream_player
```

Open http://localhost:8080

The mock compositor never swaps plates. A slow handheld drift runs on the golden-hour market. Prompts change **this** shot:

- `night falls` — light dies, lanterns take over
- `a storm rolls in` — rain, wind shake, desat
- `a dragon lands on the bridge` — the dragon crosses this frame
- `sunrise` / `golden hour` — day returns in place

**Fail next** forces a provider timeout; the shot holds. **Board** shows world state, jobs, and log.

## Engine

```bash
cd generative_livestream
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
pytest -q
```

```
POST /api/prompt          { "prompt": "a dragon lands", "provider": "mock" }
GET  /api/state
GET  /api/stream          SSE snapshots
POST /api/fail-next
```

Wire a real model with `GEMINI_API_KEY` / `LUMA_API_KEY` / `RUNWAY_API_KEY` and choose that provider. Adapters prefer native extend / loop flags. Missing keys fall back to mock.

Sora 2 is not a hard dependency (API shutdown 24 Sep 2026). Multi-viewer HLS/CDN is out of this cut.
