"""Timeline / buffer manager (spec §5.4).

The stream is a wall-clock timeline of published segments. The server always
keeps `target_buffer_sec` of finished video published ahead of the live
offset. Generated beats land in `pending` and splice at the next loop
boundary; the idle loop (or a local hold loop) fills everything else.

Why this never shows a seam while a provider takes 60-120 s:
- every loop is anchored so first frame == last frame == its anchor;
- a beat is conditioned on that anchor;
- so any number of loop repeats can play before the beat arrives and the
  join is still frame-continuous.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from collections import deque
from pathlib import Path

from . import media
from .audience import AudienceEngine, AudienceEvent, Direction
from .audience_sources import simulator, tiktok_live, twitch_irc, youtube_live_chat
from .config import settings
from .director import compose
from .models import (
    EngineSnapshot,
    EngineState,
    GenerationJob,
    LoopEntry,
    Metrics,
    Segment,
    WorldState,
)
from .pipeline import FinishedClip, finish_clip, make_hold_loop
from .providers import ProviderRejected, get_provider, provider_status
from .providers.base import GenerationRequest, ProviderError
from .safety import RateLimiter, moderate

log = logging.getLogger("livestream.timeline")


class Timeline:
    def __init__(self) -> None:
        self.world = WorldState()
        self.primary = settings.primary_provider
        self.fallback = settings.fallback_provider
        self.started = time.monotonic()
        self.seq = 0
        self.published: deque[Segment] = deque(maxlen=400)
        self.end_offset = 0.0
        self.loops: dict[str, LoopEntry] = {}
        self.loop_order: list[str] = []
        self.active_loop: str | None = None
        self.loop_rotate_i = 0
        self.pending: deque[tuple[Segment, FinishedClip, str | None]] = deque()
        self.jobs: list[GenerationJob] = []
        self.queue: deque[dict] = deque()  # {prompt, trigger, priority, user, meta}
        self.session = 1
        self.audience = AudienceEngine()
        self._source_tasks: list[asyncio.Task] = []
        self.simulate_audience = False
        self.events: deque[str] = deque(maxlen=120)
        self.limiter = RateLimiter()
        self.fail_next = False
        self.last_job_failed_at = 0.0
        self.viewers: dict[str, float] = {}
        self._inflight: set[asyncio.Task] = set()
        self._running = False
        self._ready = asyncio.Event()
        self._subscribers: set[asyncio.Queue] = set()
        self.cost_total = 0.0
        # Loop pending replacement: generated beat's clip id → hold loop id
        self._loop_after_beat: dict[str, str] = {}
        self._last_published_norm: Path | None = None
        self._last_low_log = 0.0
        self._restarting = False

    # ------------------------------------------------------------------ utils
    def log(self, msg: str) -> None:
        self.events.appendleft(msg)
        log.info(msg)
        self.broadcast({"type": "log", "msg": msg})

    def broadcast(self, payload: dict) -> None:
        for q in list(self._subscribers):
            if q.qsize() < 200:
                q.put_nowait(payload)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def live_offset(self) -> float:
        return time.monotonic() - self.started

    def buffer_ahead(self) -> float:
        return self.end_offset - self.live_offset()

    def live_segment(self) -> Segment | None:
        off = self.live_offset()
        for seg in reversed(self.published):
            if seg.start_offset <= off:
                return seg
        return None

    def newest(self) -> Segment | None:
        return self.published[-1] if self.published else None

    BEAT_TRIGGERS = ("user_prompt", "chat_vote", "gift")

    def _busy(self, trigger: str) -> bool:
        triggers = self.BEAT_TRIGGERS if trigger == "user_prompt" else (trigger,)
        return any(j.trigger in triggers and j.status in ("pending", "running") for j in self.jobs)

    # --------------------------------------------------------------- startup
    async def bootstrap(self) -> None:
        """Build the initial idle loop from the plate, then go live."""
        self.log(f"bootstrapping idle loop from plate ({settings.format} {settings.width}x{settings.height})")
        self.published.clear()
        self.pending.clear()
        self.loops.clear()
        self.loop_order.clear()
        self._loop_after_beat.clear()
        self.seq = 0
        self.active_loop = None
        plate = settings.plate_path
        if not plate.exists():
            raise RuntimeError(f"plate image missing: {plate}")
        frame = settings.media_dir / "frames" / f"plate_{settings.format}_{settings.size}.jpg"
        await media.fit_image(plate, frame)
        hold = await make_hold_loop(frame)
        self._register_loop(hold, source="hold", provider="local", title="idle · golden hour", look="golden")
        self.active_loop = hold.clip_id
        self.started = time.monotonic()
        self.end_offset = 0.0
        self._fill()
        self._ready.set()
        self.log("live")
        self._spawn(self._build_library(frame))
        self._start_sources()

    async def restart(self, fmt: str, size: str | None = None) -> None:
        """Switch output format: new session, fresh loops, client re-attaches."""
        if self._restarting:
            return
        self._restarting = True
        try:
            for t in list(self._inflight):
                t.cancel()
            self._inflight.clear()
            settings.apply_format(fmt, size)
            self.session += 1
            self._ready.clear()
            self.queue.clear()
            self.broadcast({"type": "session", "session": self.session, "format": settings.format_info()})
            await self.bootstrap()
        finally:
            self._restarting = False

    # -------------------------------------------------------------- audience
    def _start_sources(self) -> None:
        if self._source_tasks:
            return
        if settings.twitch_channel:
            self._source_tasks.append(asyncio.create_task(twitch_irc(settings.twitch_channel, self.ingest_audience)))
            self.audience.sources["twitch"] = settings.twitch_channel
        if settings.youtube_api_key and settings.youtube_video_id:
            self._source_tasks.append(asyncio.create_task(youtube_live_chat(settings.youtube_api_key, settings.youtube_video_id, self.ingest_audience)))
            self.audience.sources["youtube"] = settings.youtube_video_id
        if settings.tiktok_unique_id:
            self._source_tasks.append(asyncio.create_task(tiktok_live(settings.tiktok_unique_id, self.ingest_audience)))
            self.audience.sources["tiktok"] = "@" + settings.tiktok_unique_id
        self.audience.sources["webhook"] = "POST /api/audience/event"

    def set_simulator(self, on: bool) -> None:
        if on and not self.simulate_audience:
            self.simulate_audience = True
            self._sim_task = asyncio.create_task(simulator(self.ingest_audience))
            self.audience.sources["simulator"] = "on"
            self.log("audience simulator on")
        elif not on and self.simulate_audience:
            self.simulate_audience = False
            self._sim_task.cancel()
            self.audience.sources.pop("simulator", None)
            self.log("audience simulator off")

    def ingest_audience(self, ev: AudienceEvent) -> None:
        direction = self.audience.ingest(ev)
        self.broadcast({"type": "audience_event", "event": {
            "platform": ev.platform, "type": ev.type, "user": ev.user, "text": ev.text,
            "gift_name": ev.gift_name, "gift_value": ev.gift_value, "count": ev.count, "ts": ev.ts}})
        if direction:
            self._apply_direction(direction)

    def _apply_direction(self, d: Direction) -> None:
        if not moderate(d.prompt).ok:
            return
        item = {"prompt": d.prompt, "trigger": d.trigger, "priority": d.priority, "user": d.user, "meta": d.meta}
        if d.priority == "interrupt":
            self.queue.appendleft(item)
        else:
            self.queue.append(item)
        self.log(f"{d.trigger} from {d.user}: {d.prompt[:60]}")
        self.broadcast({"type": "queue", "queue": [q["prompt"] for q in self.queue]})
        self.broadcast({"type": "direction", "direction": item})

    async def _build_library(self, frame: Path) -> None:
        """Ask the loop provider for a small library of real idle loops."""
        provider_name = settings.loop_provider or self.primary
        provider = get_provider(provider_name)
        if not provider.configured() or not provider.supports_loop:
            self.log(f"{provider_name} has no loop support/keys — keeping local hold loop")
            return
        for i in range(settings.loop_library_size):
            job = self._begin_job("library", f"idle loop {i + 1}")
            try:
                directed = await compose(self.world, "ambient idle loop", loop=True)
                job.director_prompt_final = directed.generation_prompt
                res = await provider.generate(
                    GenerationRequest(
                        prompt=directed.generation_prompt,
                        duration_sec=settings.loop_sec,
                        condition_frame=frame,
                        loop=True,
                        look_hint={"from": "golden", "to": "golden"},
                    )
                )
                fin = await finish_clip(res, prev_norm=None, is_loop=True, target_duration=settings.loop_sec)
                self._register_loop(fin, source="idle", provider=res.provider, title=f"idle loop {i + 1} · golden hour", look="golden")
                self._complete_job(job, res.provider, fin.duration_sec)
                if self.active_loop and self.loops[self.active_loop].source == "hold":
                    self.active_loop = fin.clip_id
                    self.log(f"idle library loop {fin.clip_id} is now active")
            except Exception as exc:
                self._fail_job(job, exc)

    def _register_loop(self, fin: FinishedClip, *, source: str, provider: str, title: str, look: str) -> LoopEntry:
        entry = LoopEntry(
            loop_id=fin.clip_id,
            frag_path=str(fin.frag_path),
            norm_path=str(fin.norm_path),
            anchor_frame=str(fin.last_frame),
            duration_sec=fin.duration_sec,
            title=title,
            source=source,  # type: ignore[arg-type]
            provider=provider,
            look=look,
        )
        self.loops[entry.loop_id] = entry
        if source == "idle":
            self.loop_order.append(entry.loop_id)
        return entry

    # -------------------------------------------------------------- publish
    def _publish(self, *, url: str, frame_url: str, duration: float, source: str, title: str,
                 prompt_used: str = "", provider: str = "local", loop_id: str | None = None,
                 conditioning: str | None = None, native_extend: bool = False, norm_path: Path | None = None) -> Segment:
        self.seq += 1
        seg = Segment(
            seq=self.seq,
            segment_id=f"seg_{self.seq:05d}",
            source=source,  # type: ignore[arg-type]
            prompt_used=prompt_used,
            duration_sec=duration,
            start_offset=self.end_offset,
            title=title,
            url=url,
            frame_url=frame_url,
            conditioning_frame=conditioning,
            model_provider=provider,
            native_extend=native_extend,
            loop_candidate=source != "generated",
            loop_id=loop_id,
        )
        self.end_offset += duration
        self.published.append(seg)
        self._last_published_norm = norm_path
        self.broadcast({"type": "segment", "segment": seg.model_dump(mode="json")})
        return seg

    def _publish_loop(self, loop_id: str) -> Segment:
        loop = self.loops[loop_id]
        return self._publish(
            url=f"/api/media/loops/{Path(loop.frag_path).name}",
            frame_url=f"/api/frames/{Path(loop.anchor_frame).name}",
            duration=loop.duration_sec,
            source=loop.source,
            title=loop.title,
            provider=loop.provider,
            loop_id=loop_id,
            norm_path=Path(loop.norm_path),
        )

    def _next_idle_loop(self) -> str:
        """Rotate through library loops that share the active anchor look."""
        active = self.loops[self.active_loop] if self.active_loop else None
        if active and active.source == "idle":
            same = [lid for lid in self.loop_order if self.loops[lid].look == active.look]
            if len(same) > 1:
                self.loop_rotate_i = (self.loop_rotate_i + 1) % len(same)
                return same[self.loop_rotate_i]
        return self.active_loop  # type: ignore[return-value]

    def _fill(self) -> None:
        guard = 0
        while self.buffer_ahead() < settings.target_buffer_sec and guard < 12:
            guard += 1
            if self.pending:
                seg_tpl, fin, hold_loop_id = self.pending.popleft()
                seg = self._publish(
                    url=seg_tpl.url,
                    frame_url=seg_tpl.frame_url,
                    duration=fin.duration_sec,
                    source="generated",
                    title=seg_tpl.title,
                    prompt_used=seg_tpl.prompt_used,
                    provider=seg_tpl.model_provider,
                    conditioning=seg_tpl.conditioning_frame,
                    native_extend=seg_tpl.native_extend,
                    norm_path=fin.norm_path,
                )
                if hold_loop_id:
                    self.active_loop = hold_loop_id
                self.log(f"spliced {seg.segment_id} ({seg.model_provider}) at +{seg.start_offset:.1f}s")
                continue
            if not self.active_loop:
                break
            self._publish_loop(self._next_idle_loop())

    # ------------------------------------------------------------------ jobs
    def _begin_job(self, trigger: str, prompt: str, *, condition: Path | None = None, user: str = "director", meta: dict | None = None) -> GenerationJob:
        job = GenerationJob(
            job_id=f"job_{uuid.uuid4().hex[:8]}",
            trigger=trigger,  # type: ignore[arg-type]
            user_prompt_raw=prompt,
            condition_frame=str(condition) if condition else None,
            provider=self.primary,
            status="running",
            requested_by=user,
            meta=meta or {},
        )
        self.jobs.insert(0, job)
        del self.jobs[40:]
        self.broadcast({"type": "job", "job": job.model_dump(mode="json")})
        return job

    def _complete_job(self, job: GenerationJob, provider: str, seconds: float) -> None:
        job.status = "complete"
        job.provider = provider
        job.latency_ms = int((time.time() - job.created_at.timestamp()) * 1000)
        job.cost_usd = round(settings.cost_per_sec.get(provider.split("(")[0], 0.0) * seconds, 4)
        self.cost_total += job.cost_usd
        self.broadcast({"type": "job", "job": job.model_dump(mode="json")})

    def _fail_job(self, job: GenerationJob, exc: Exception) -> None:
        job.status = "rejected" if isinstance(exc, ProviderRejected) else "failed"
        job.error = str(exc)[:300]
        job.latency_ms = int((time.time() - job.created_at.timestamp()) * 1000)
        self.last_job_failed_at = time.monotonic()
        self.log(f"{job.trigger} {job.status}: {job.error}")
        self.broadcast({"type": "job", "job": job.model_dump(mode="json")})

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    def submit(self, prompt: str, provider: str | None = None, session: str = "default") -> dict:
        safety = moderate(prompt)
        if not safety.ok:
            self.log(f"prompt rejected ({safety.reason})")
            return {"ok": False, "reason": safety.reason}
        if not self.limiter.allow(session):
            return {"ok": False, "reason": "rate"}
        if provider and provider in provider_status():
            self.primary = provider
        self.queue.append({"prompt": prompt.strip(), "trigger": "user_prompt", "priority": "queue", "user": session, "meta": {}})
        self.log(f"queued: {prompt.strip()}")
        self.broadcast({"type": "queue", "queue": [q["prompt"] for q in self.queue]})
        return {"ok": True}

    def _anchor(self) -> tuple[Path, Path | None, dict | None, str | None]:
        """Frame the next beat must start from + prior clip info for native extend."""
        if self.pending:
            seg, fin, _ = self.pending[-1]
            return fin.last_frame, fin.norm_path, getattr(fin, "ref", None), seg.model_provider
        loop = self.loops[self.active_loop]  # type: ignore[index]
        return Path(loop.anchor_frame), Path(loop.norm_path), None, None

    async def _generate_with_fallback(self, req: GenerationRequest, job: GenerationJob):
        if self.fail_next:
            self.fail_next = False
            raise ProviderError("simulated provider timeout")
        primary = get_provider(self.primary)
        try:
            return await primary.generate(req)
        except ProviderRejected:
            raise
        except Exception as exc:
            if self.fallback and self.fallback != self.primary:
                self.log(f"{self.primary} failed ({str(exc)[:80]}); falling back to {self.fallback}")
                job.retries += 1
                return await get_provider(self.fallback).generate(req)
            raise

    async def _run_prompt_job(self, item: dict) -> None:
        prompt = item["prompt"]
        anchor, prior_clip, prior_ref, prior_provider = self._anchor()
        job = self._begin_job(item.get("trigger", "user_prompt"), prompt, condition=anchor, user=item.get("user", "director"), meta=item.get("meta"))
        try:
            directed = await compose(self.world, prompt, audience=self.audience.director_context())
            job.director_prompt_final = directed.generation_prompt
            job.substituted = directed.substituted
            req = GenerationRequest(
                prompt=directed.generation_prompt,
                duration_sec=settings.segment_sec,
                condition_frame=anchor,
                prior_clip=prior_clip,
                prior_ref=prior_ref,
                prior_provider=prior_provider,
                look_hint={"from": self.world.look, "to": directed.look},
            )
            res = await self._generate_with_fallback(req, job)
            fin = await finish_clip(res, prev_norm=prior_clip, is_loop=False, target_duration=settings.segment_sec)
            setattr(fin, "ref", res.ref)
            # Never-blank: a local breathing loop on the beat's last frame is
            # ready *before* the beat is published, so the tail can't freeze.
            hold = await make_hold_loop(fin.last_frame)
            self._register_loop(hold, source="hold", provider="local", title=f"hold · {directed.look}", look=directed.look)
            tpl = Segment(
                seq=0, segment_id="pending", source="generated",
                prompt_used=directed.generation_prompt, duration_sec=fin.duration_sec, start_offset=0,
                title=(f"🎁 {item.get('user')}: " if item.get("trigger") == "gift" else (f"chat: " if item.get("trigger") == "chat_vote" else "")) + f"{prompt[:44]} · {directed.look}",
                url=f"/api/media/segments/{fin.frag_path.name}",
                frame_url=f"/api/frames/{fin.last_frame.name}",
                conditioning_frame=f"/api/frames/{anchor.name}",
                model_provider=res.provider, native_extend=res.native_extend,
            )
            self.pending.append((tpl, fin, hold.clip_id))
            self.world = directed.updated_world_state
            self._complete_job(job, res.provider, fin.duration_sec)
            self.log(f"beat ready {fin.clip_id} via {res.provider} ({job.latency_ms} ms)")
            self.broadcast({"type": "world", "world": self.world.model_dump(mode="json")})
            if not self.queue:
                self._spawn(self._run_loop_job(fin, directed.look))
        except ProviderRejected as exc:
            self._fail_job(job, exc)
            self.broadcast({"type": "toast", "msg": "That direction was declined by the video model. Try another.", "kind": "warn"})
        except Exception as exc:
            self._fail_job(job, exc)
            if job.retries < 1 and not isinstance(exc, media.MediaError):
                job.retries += 1
                self.queue.appendleft(item)
                self.log("retrying prompt once")
            else:
                self.broadcast({"type": "toast", "msg": "Generation missed. Holding the shot.", "kind": "warn"})

    async def _run_loop_job(self, beat: FinishedClip, look: str) -> None:
        """Generate a real idle loop anchored to the beat's last frame."""
        provider_name = settings.loop_provider or self.primary
        provider = get_provider(provider_name)
        job = self._begin_job("loop", f"new idle loop · {look}", condition=beat.last_frame)
        if not provider.configured():
            job.status = "complete"
            job.error = "no loop provider configured; local hold loop stays active"
            self.broadcast({"type": "job", "job": job.model_dump(mode="json")})
            return
        try:
            directed = await compose(self.world, "ambient idle loop", loop=True)
            job.director_prompt_final = directed.generation_prompt
            req = GenerationRequest(
                prompt=directed.generation_prompt,
                duration_sec=settings.loop_sec,
                condition_frame=beat.last_frame,
                loop=True,
                prior_clip=beat.norm_path,
                prior_ref=getattr(beat, "ref", None),
                look_hint={"from": look, "to": look},
            )
            res = await provider.generate(req)
            fin = await finish_clip(res, prev_norm=beat.norm_path, is_loop=True, target_duration=settings.loop_sec)
            if not res.native_loop:
                # Provider couldn't guarantee first == last frame; close it locally.
                closed = await media.crossfade_head(fin.norm_path, fin.norm_path, fin.norm_path.with_name(fin.norm_path.stem + "_closed.mp4"), duration=0.5)
                fin.norm_path = closed
                fin.frag_path = await media.fragment(closed, fin.frag_path)
                await media.last_frame(closed, fin.last_frame)
            self._register_loop(fin, source="idle", provider=res.provider, title=f"idle · {look}", look=look)
            self._complete_job(job, res.provider, fin.duration_sec)
            # Swap in once the beat this anchors to has been published.
            self._loop_after_beat[beat.clip_id] = fin.clip_id
            self.log(f"new loop {fin.clip_id} anchored to {beat.clip_id}")
        except Exception as exc:
            self._fail_job(job, exc)

    async def _pump(self) -> None:
        vote = self.audience.tick()
        if vote:
            self._apply_direction(vote)
        if not self._busy("user_prompt") and self.queue:
            item = self.queue.popleft()
            self.broadcast({"type": "queue", "queue": [q["prompt"] for q in self.queue]})
            self._spawn(self._run_prompt_job(item))
        # Promote a finished provider loop over the hold loop once its beat is live.
        for beat_id, loop_id in list(self._loop_after_beat.items()):
            hold_active = self.active_loop and self.loops[self.active_loop].source == "hold"
            beat_published = any(s.source == "generated" and beat_id in s.url for s in self.published)
            if hold_active and beat_published:
                self.active_loop = loop_id
                self._loop_after_beat.pop(beat_id, None)
                self.log(f"idle loop {loop_id} active")

    # ---------------------------------------------------------------- state
    def state(self) -> EngineState:
        buf = self.buffer_ahead()
        live = self.live_segment()
        recently_failed = time.monotonic() - self.last_job_failed_at < 20 and not self._busy("user_prompt")
        if buf < settings.low_buffer_sec * 0.45:
            return EngineState.BUFFER_LOW
        if recently_failed and self.jobs and self.jobs[0].status in ("failed", "rejected"):
            return EngineState.ERROR_RECOVERY
        if live and live.source == "generated":
            return EngineState.PLAYING_GENERATED
        off = self.live_offset()
        if self.pending or any(s.source == "generated" and s.start_offset > off for s in self.published):
            return EngineState.SPLICING
        if self._busy("user_prompt"):
            return EngineState.PROMPT_QUEUED
        if self.active_loop and self.loops[self.active_loop].source == "hold" and self._busy("loop"):
            return EngineState.GENERATING_NEW_LOOP
        return EngineState.IDLE_LOOP

    def snapshot(self) -> EngineSnapshot:
        live = self.live_segment()
        return EngineSnapshot(
            state=self.state(),
            buffer_ahead_sec=round(self.buffer_ahead(), 2),
            live_offset_sec=round(self.live_offset(), 2),
            live_seq=live.seq if live else 0,
            now_playing=live.title if live else "",
            world=self.world,
            jobs=self.jobs[:10],
            queue=[q["prompt"] for q in self.queue],
            active_loop=self.active_loop,
            providers=provider_status(),
            primary_provider=self.primary,
            events=list(self.events)[:14],
            session=self.session,
            format=settings.format_info(),
            audience=self.audience.snapshot(),
        )

    def metrics(self) -> Metrics:
        lat: dict[str, list[int]] = {}
        ok: dict[str, list[int]] = {}
        for j in self.jobs:
            if j.status in ("complete", "failed", "rejected") and j.trigger != "library":
                p = j.provider.split("(")[0]
                ok.setdefault(p, []).append(1 if j.status == "complete" else 0)
                if j.status == "complete" and j.latency_ms:
                    lat.setdefault(p, []).append(j.latency_ms)
        now = time.monotonic()
        self.viewers = {k: v for k, v in self.viewers.items() if now - v < 15}
        return Metrics(
            buffer_ahead_sec=round(self.buffer_ahead(), 2),
            live_offset_sec=round(self.live_offset(), 2),
            segments_published=self.seq,
            jobs_total=len([j for j in self.jobs if j.trigger != "library"]),
            jobs_failed=len([j for j in self.jobs if j.status == "failed"]),
            jobs_rejected=len([j for j in self.jobs if j.status == "rejected"]),
            avg_latency_ms={k: round(sum(v) / len(v)) for k, v in lat.items() if v},
            success_rate={k: round(sum(v) / len(v), 3) for k, v in ok.items() if v},
            cost_usd_total=round(self.cost_total, 4),
            loops_in_library=len(self.loop_order),
            viewers=len(self.viewers),
        )

    def playlist(self, after_seq: int = 0, limit: int = 40) -> list[Segment]:
        return [s for s in self.published if s.seq > after_seq][:limit]

    def heartbeat(self, viewer_id: str) -> None:
        self.viewers[viewer_id] = time.monotonic()

    # ------------------------------------------------------------------ loop
    async def run_forever(self) -> None:
        self._running = True
        await self.bootstrap()
        while self._running:
            try:
                if not self._ready.is_set():
                    await asyncio.sleep(0.25)
                    continue
                self._fill()
                await self._pump()
                if self.buffer_ahead() < settings.low_buffer_sec and time.monotonic() - self._last_low_log > 5:
                    self._last_low_log = time.monotonic()
                    self.log(f"buffer low: {self.buffer_ahead():.1f}s")
            except Exception as exc:  # keep the stream alive no matter what
                log.exception("timeline tick failed: %s", exc)
            await asyncio.sleep(0.25)

    def stop(self) -> None:
        self._running = False
        for t in list(self._inflight) + self._source_tasks:
            t.cancel()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    def reset_media(self) -> None:
        for sub in ("segments", "frames", "raw", "tmp"):
            shutil.rmtree(settings.media_dir / sub, ignore_errors=True)
            (settings.media_dir / sub).mkdir(exist_ok=True)


timeline = Timeline()
