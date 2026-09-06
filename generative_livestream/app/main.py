from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .buffer import engine
from .models import PromptRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

PLAYER_DIR = Path(__file__).resolve().parents[2] / "livestream_player"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(engine.run_forever())
    yield
    engine.stop()
    task.cancel()


app = FastAPI(title="Aether Feed", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if PLAYER_DIR.exists():
    app.mount("/assets", StaticFiles(directory=PLAYER_DIR / "assets"), name="assets")


@app.get("/api/health")
async def health():
    return {"ok": True, "state": engine.state.value, "buffer": engine.buffer_ahead()}


@app.get("/api/state")
async def state():
    snap = engine.snapshot()
    return snap.model_dump(mode="json") | {"events": list(engine.events)}


@app.post("/api/prompt")
async def prompt(body: PromptRequest):
    result = engine.submit(body.prompt, body.provider)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["reason"])
    return result | {"state": engine.snapshot().model_dump(mode="json")}


@app.post("/api/fail-next")
async def fail_next():
    engine.fail_next = True
    return {"ok": True}


@app.get("/api/stream")
async def stream():
    async def gen():
        while True:
            payload = engine.snapshot().model_dump(mode="json")
            payload["events"] = list(engine.events)
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream")


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
    target = (PLAYER_DIR / name).resolve()
    if PLAYER_DIR.resolve() not in target.parents and target != PLAYER_DIR.resolve():
        raise HTTPException(404)
    if not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)
