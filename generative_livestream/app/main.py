from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from .config import settings
from .audience import AudienceEvent
from .config import FORMATS, SIZES
from .models import AudienceEventIn, FormatRequest, PromptRequest
from .timeline import timeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

PLAYER_DIR = Path(__file__).resolve().parents[2] / "livestream_player"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(timeline.run_forever())
    yield
    timeline.stop()
    task.cancel()


app = FastAPI(title="Aether Feed", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _snapshot_payload() -> dict:
    return timeline.snapshot().model_dump(mode="json")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "ready": timeline._ready.is_set(),
        "state": timeline.state().value,
        "buffer_ahead_sec": round(timeline.buffer_ahead(), 2),
        "primary_provider": timeline.primary,
        "session": timeline.session,
        "format": settings.format_info(),
    }


@app.get("/api/state")
async def state():
    return _snapshot_payload()


@app.get("/api/metrics")
async def metrics():
    return timeline.metrics().model_dump(mode="json")


@app.get("/api/playlist")
async def playlist(after: int = 0, limit: int = 40):
    """Live manifest: segments with seq > after, plus where the live edge is."""
    await timeline.wait_ready()
    segs = timeline.playlist(after_seq=after, limit=limit)
    live = timeline.live_segment()
    return {
        "session": timeline.session,
        "live_offset_sec": round(timeline.live_offset(), 3),
        "live_seq": live.seq if live else 0,
        "end_offset_sec": round(timeline.end_offset, 3),
        "segments": [s.model_dump(mode="json") for s in segs],
        "codec": settings.mime,
        "format": settings.format_info(),
    }


@app.post("/api/prompt")
async def prompt(body: PromptRequest, request: Request):
    session = request.headers.get("x-viewer-id") or (request.client.host if request.client else "default")
    result = timeline.submit(body.prompt, body.provider, session=session)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["reason"])
    return result | {"state": _snapshot_payload()}


@app.get("/api/format")
async def get_format():
    return settings.format_info() | {"session": timeline.session}


@app.post("/api/format")
async def set_format(body: FormatRequest):
    """Switch aspect ratio / size. Starts a new session; the player re-attaches."""
    if body.format not in FORMATS:
        raise HTTPException(400, "unknown format")
    if body.size is not None and body.size not in SIZES:
        raise HTTPException(400, "unknown size")
    if body.format == settings.format and (body.size is None or body.size == settings.size):
        return settings.format_info() | {"session": timeline.session, "changed": False}
    asyncio.create_task(timeline.restart(body.format, body.size))
    return settings.format_info() | {"session": timeline.session + 1, "changed": True}


@app.get("/api/audience")
async def audience_state():
    return timeline.audience.snapshot()


@app.post("/api/audience/event")
async def audience_event(body: AudienceEventIn):
    """Generic webhook for any platform relay (TikTok, Twitch, YouTube, Kick, ...)."""
    timeline.ingest_audience(AudienceEvent(
        platform=body.platform, type=body.type, user=body.user[:40], text=body.text[:280],
        gift_name=body.gift_name[:40], gift_value=max(0, body.gift_value), count=max(1, body.count),
    ))
    return {"ok": True, "audience": timeline.audience.snapshot()}


@app.post("/api/audience/simulate/{state}")
async def audience_simulate(state: str):
    timeline.set_simulator(state in ("on", "1", "true", "start"))
    return {"ok": True, "simulating": timeline.simulate_audience}


@app.post("/api/fail-next")
async def fail_next():
    timeline.fail_next = True
    return {"ok": True}


@app.post("/api/provider/{name}")
async def set_provider(name: str):
    from .providers import provider_status

    if name not in provider_status():
        raise HTTPException(404, "unknown provider")
    timeline.primary = name
    timeline.log(f"primary provider → {name}")
    return {"ok": True, "primary_provider": name}


@app.post("/api/heartbeat/{viewer_id}")
async def heartbeat(viewer_id: str):
    timeline.heartbeat(viewer_id)
    return {"ok": True}


@app.get("/api/events")
async def events():
    """SSE: snapshot every 0.5s plus discrete segment/job/log/toast events."""
    q = timeline.subscribe()

    async def gen():
        try:
            yield f"event: snapshot\ndata: {json.dumps(_snapshot_payload())}\n\n"
            last = asyncio.get_running_loop().time()
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=0.5)
                    yield f"event: {item['type']}\ndata: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    pass
                now = asyncio.get_running_loop().time()
                if now - last >= 0.5:
                    last = now
                    yield f"event: snapshot\ndata: {json.dumps(_snapshot_payload())}\n\n"
        finally:
            timeline.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _safe_child(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404)
    return target


@app.get("/api/media/{kind}/{name}")
async def media_file(kind: str, name: str):
    if kind not in ("loops", "segments"):
        raise HTTPException(404)
    return FileResponse(_safe_child(settings.media_dir / kind, name), media_type="video/webm" if name.endswith(".webm") else "video/mp4",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/frames/{name}")
async def frame_file(name: str):
    return FileResponse(_safe_child(settings.media_dir / "frames", name), media_type="image/jpeg")


@app.get("/")
async def index():
    index = PLAYER_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "player not built")
    return FileResponse(index)


@app.get("/{name:path}")
async def player_static(name: str):
    if name.startswith("api/"):
        raise HTTPException(404)
    return FileResponse(_safe_child(PLAYER_DIR, name))
