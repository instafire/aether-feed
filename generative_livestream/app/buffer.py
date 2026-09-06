from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque

from .director import compose
from .models import (
    EngineSnapshot,
    EngineState,
    GenerationJob,
    Segment,
    WorldState,
)
from .providers import get_provider
from .providers.base import GenerationRequest
from .safety import RateLimiter, moderate

log = logging.getLogger("livestream.buffer")

TARGET_BUFFER = 22.0
LOW_BUFFER = 8.0
CRITICAL_BUFFER = 3.5
SEGMENT_SEC = 6.4

IDLE_ASSETS = [
    ("idle_market", "/assets/idle_market.jpg", "golden market idle",
     "lantern-lit wooden stalls of a floating market above an endless sea of clouds"),
    ("idle_courier", "/assets/idle_courier.jpg", "courier on the bridge",
     "the fox-eared courier in a blue traveling cloak walks a rope bridge toward the market"),
    ("idle_clouds", "/assets/idle_clouds.jpg", "cloud-sea aerial",
     "aerial drift over a sea of clouds with distant floating islands"),
]

SCENE_ASSETS = {
    "scene_dragon": ("/assets/scene_dragon.jpg", "dragon on the bridge",
                     "an amber-scaled dragon lands on the rope bridge"),
    "scene_night": ("/assets/scene_night.jpg", "night market",
                    "the floating market at night under a star-filled sky"),
    "scene_rain": ("/assets/scene_rain.jpg", "rain on the boards",
                   "rain sheets across wet wooden market boards"),
}


class BufferManager:
    """Always keep N seconds of ready video ahead of the playhead (spec §5.4)."""

    def __init__(self) -> None:
        self.state = EngineState.IDLE_LOOP
        self.world = WorldState()
        self.queue: deque[str] = deque()
        self.jobs: list[GenerationJob] = []
        self.segments: deque[Segment] = deque()
        self.current: Segment | None = None
        self.playhead = 0.0
        self.provider_name = "mock"
        self.fallback_name = "mock"
        self.fail_next = False
        self.limiter = RateLimiter()
        self._seg_n = 0
        self._idle_i = 0
        self._lock = asyncio.Lock()
        self._running = False
        self._inflight: set[asyncio.Task] = set()
        self.events: deque[str] = deque(maxlen=80)
        self.prime()

    def log(self, msg: str) -> None:
        self.events.appendleft(msg)
        log.info(msg)

    def _id(self) -> str:
        self._seg_n += 1
        return f"seg_{self._seg_n:05d}"

    def prime(self) -> None:
        self.segments.clear()
        depth = 0.0
        while depth < TARGET_BUFFER:
            seg = self._idle_segment()
            self.segments.append(seg)
            depth += seg.duration_sec
        self.current = self.segments.popleft()
        self.playhead = 0.0
        self.state = EngineState.IDLE_LOOP
        self.log("idle library primed")

    def _idle_segment(self, hold: bool = False) -> Segment:
        name, url, title, frame = IDLE_ASSETS[self._idle_i % len(IDLE_ASSETS)]
        self._idle_i += 1
        return Segment(
            segment_id=self._id(),
            source="idle",
            prompt_used=f"idle:{name}",
            duration_sec=8.0 * (1.15 if hold else 1.0),
            title=title + (" (hold)" if hold else ""),
            asset_url=url,
            last_frame=frame,
            loop_candidate=True,
            model_provider="cached",
        )

    def buffer_ahead(self) -> float:
        remaining = 0.0
        if self.current:
            remaining += max(0.0, self.current.duration_sec - self.playhead)
        remaining += sum(s.duration_sec for s in self.segments)
        return remaining

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            state=self.state,
            buffer_ahead_sec=round(self.buffer_ahead(), 2),
            now_playing=self.current.title if self.current else "",
            world=self.world,
            jobs=list(self.jobs[:8]),
            queue=list(self.queue),
            segments_ready=len(self.segments),
        )

    def extend_tail(self) -> None:
        seg = self._idle_segment(hold=True)
        self.segments.append(seg)
        self.log(f"buffer hold — {seg.segment_id}")

    def refill_idle(self) -> None:
        if self.buffer_ahead() < TARGET_BUFFER:
            self.segments.append(self._idle_segment())

    def tick(self, dt: float) -> None:
        if not self.current:
            return
        stretch = 1.0
        ahead = self.buffer_ahead()
        if ahead < CRITICAL_BUFFER:
            stretch = 0.72
        elif ahead < LOW_BUFFER:
            stretch = 0.88
        self.playhead += dt * stretch
        if self.state in (EngineState.IDLE_LOOP, EngineState.PROMPT_QUEUED):
            self.refill_idle()
        if self.buffer_ahead() < CRITICAL_BUFFER:
            self.extend_tail()
            if self.state not in (
                EngineState.PROMPT_QUEUED,
                EngineState.GENERATING_NEW_LOOP,
                EngineState.SPLICING,
                EngineState.ERROR_RECOVERY,
            ):
                self.state = EngineState.BUFFER_LOW
        if self.current and self.playhead >= self.current.duration_sec:
            self.advance()

    def advance(self) -> None:
        nxt = self.segments.popleft() if self.segments else self._idle_segment(hold=True)
        if self.state == EngineState.SPLICING and nxt.source == "generated":
            self.state = EngineState.PLAYING_GENERATED
        elif self.state == EngineState.PLAYING_GENERATED and nxt.source == "idle":
            self.state = EngineState.IDLE_LOOP
        elif self.state == EngineState.GENERATING_NEW_LOOP and nxt.loop_candidate:
            self.state = EngineState.IDLE_LOOP
        elif self.state == EngineState.ERROR_RECOVERY and nxt.loop_candidate:
            self.state = EngineState.IDLE_LOOP
        self.current = nxt
        self.playhead = 0.0
        self.world.last_frame_description = nxt.last_frame or self.world.last_frame_description

    def submit(self, prompt: str, provider: str | None = None) -> dict:
        safety = moderate(prompt)
        if not safety.ok:
            return {"ok": False, "reason": safety.reason}
        if not self.limiter.allow():
            return {"ok": False, "reason": "rate"}
        if provider:
            self.provider_name = provider
        self.queue.append(prompt.strip())
        self.log(f"queued: {prompt.strip()}")
        if self.state in (EngineState.IDLE_LOOP, EngineState.ERROR_RECOVERY, EngineState.BUFFER_LOW):
            self.state = EngineState.PROMPT_QUEUED
        return {"ok": True}

    def _busy(self, trigger: str) -> bool:
        return any(
            j.trigger == trigger and j.status in ("pending", "running") for j in self.jobs
        )

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def _begin_job(self, trigger: str, prompt: str) -> GenerationJob:
        newest = self.segments[-1] if self.segments else self.current
        job = GenerationJob(
            job_id=f"job_{uuid.uuid4().hex[:8]}",
            trigger=trigger,  # type: ignore[arg-type]
            user_prompt_raw=prompt,
            condition_on_segment=newest.segment_id if newest else None,
            provider=self.provider_name,
            status="running",
        )
        self.jobs.insert(0, job)
        self.jobs[:] = self.jobs[:24]
        return job

    async def pump(self) -> None:
        if self._busy("user_prompt"):
            return
        if self.queue:
            prompt = self.queue.popleft()
            job = self._begin_job("user_prompt", prompt)
            self._spawn(self.run_job(job))
            return
        if (
            self.state == EngineState.PLAYING_GENERATED
            and not self._busy("loop")
            and not self.queue
        ):
            self.state = EngineState.GENERATING_NEW_LOOP
            job = self._begin_job(
                "loop", "settle into a seamless ambient loop from the last frame"
            )
            self._spawn(self.run_job(job))

    async def run_job(self, job: GenerationJob) -> None:
        prompt = job.user_prompt_raw
        trigger = job.trigger
        t0 = time.monotonic()
        directed = await compose(self.world, prompt)
        job.director_prompt_final = directed.generation_prompt
        job.substituted = directed.substituted
        try:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("provider timeout")
            result = await self._generate(directed.generation_prompt, loop=(trigger == "loop"))
            self.world = directed.updated_world_state
            if trigger == "loop":
                name, url, title, frame = IDLE_ASSETS[self._idle_i % len(IDLE_ASSETS)]
                self._idle_i += 1
                seg = Segment(
                    segment_id=self._id(),
                    source="idle",
                    prompt_used=directed.generation_prompt,
                    duration_sec=SEGMENT_SEC,
                    title=title,
                    asset_url=result.asset_url if result.asset_url.startswith("http") else url,
                    last_frame=frame,
                    conditioning_frame=job.condition_on_segment,
                    model_provider=result.provider,
                    loop_candidate=True,
                )
            else:
                scene_id = directed.scene_id or "scene_dragon"
                url, title, frame = SCENE_ASSETS.get(
                    scene_id, SCENE_ASSETS["scene_dragon"]
                )
                seg = Segment(
                    segment_id=self._id(),
                    source="generated",
                    prompt_used=directed.generation_prompt,
                    duration_sec=SEGMENT_SEC,
                    title=title,
                    asset_url=result.asset_url if result.asset_url.startswith("http") else url,
                    last_frame=frame,
                    conditioning_frame=job.condition_on_segment,
                    model_provider=result.provider,
                    loop_candidate=False,
                )
                self.state = EngineState.SPLICING
            self.segments.append(seg)
            job.status = "complete"
            job.latency_ms = int((time.monotonic() - t0) * 1000)
            self.log(f"spliced {seg.segment_id} via {seg.model_provider}")
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.latency_ms = int((time.monotonic() - t0) * 1000)
            self.state = EngineState.ERROR_RECOVERY
            self.extend_tail()
            self.log(f"job failed: {exc}")
            if job.trigger == "user_prompt" and job.retries < 1:
                job.retries += 1
                self.queue.appendleft(prompt)

    async def _generate(self, prompt: str, loop: bool):
        newest = self.segments[-1] if self.segments else self.current
        req = GenerationRequest(
            prompt=prompt,
            duration_sec=SEGMENT_SEC,
            condition_image_url=newest.asset_url if newest else None,
            loop=loop,
        )
        primary = get_provider(self.provider_name)
        try:
            return await primary.generate(req)
        except Exception as exc:
            self.log(f"{self.provider_name} failed ({exc}); trying {self.fallback_name}")
            return await get_provider(self.fallback_name).generate(req)

    async def run_forever(self) -> None:
        self._running = True
        last = time.monotonic()
        while self._running:
            now = time.monotonic()
            dt = min(0.08, now - last)
            last = now
            self.tick(dt)
            await self.pump()
            await asyncio.sleep(0.05)

    def stop(self) -> None:
        self._running = False


engine = BufferManager()
