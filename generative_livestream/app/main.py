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
from .models import PromptRequest
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
        "live_offset_sec": round(timeline.live_offset(), 3),
        "live_seq": live.seq if live else 0,
        "end_offset_sec": round(timeline.end_offset, 3),
        "segments": [s.model_dump(mode="json") for s in segs],
        "codec": settings.mime,
    }


@app.post("/api/prompt")
async def prompt(body: PromptRequest, request: Request):
    session = request.headers.get("x-viewer-id") or (request.client.host if request.client else "default")
    result = timeline.submit(body.prompt, body.provider, session=session)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["reason"])
    return result | {"state": _snapshot_payload()}


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
