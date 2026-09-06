from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

os.environ.setdefault("VIDEO_WIDTH", "480")
os.environ.setdefault("VIDEO_HEIGHT", "270")
os.environ.setdefault("SEGMENT_SEC", "2")
os.environ.setdefault("LOOP_SEC", "2")
os.environ.setdefault("TARGET_BUFFER_SEC", "6")
os.environ.setdefault("LOOP_LIBRARY_SIZE", "1")
os.environ.setdefault("CHAT_WINDOW_SEC", "0.2")
os.environ.setdefault("GIFT_COOLDOWN_SEC", "0")
os.environ.setdefault("MEDIA_DIR", str(Path(__file__).resolve().parent / "_media"))

from app import media  # noqa: E402
from app.audience import AudienceEngine, AudienceEvent  # noqa: E402
from app.config import resolve_dims, settings  # noqa: E402
from app.director import compose_local  # noqa: E402
from app.models import WorldState  # noqa: E402
from app.providers.kling import kling_jwt  # noqa: E402
from app.safety import moderate  # noqa: E402
from app.timeline import Timeline  # noqa: E402


def test_moderate_blocks_policy():
    assert moderate("a dragon lands").ok
    assert not moderate("explicit sex scene").ok
    assert not moderate("").ok


def test_director_keeps_character_verbatim_and_infers_look():
    world = WorldState()
    result = compose_local(world, "night falls over the market", audience={"energy": "high"})
    assert "orange fur, blue traveling cloak, brass goggles" in result.generation_prompt
    assert "Starting from exactly this frame" in result.generation_prompt
    assert "lively" in result.generation_prompt
    assert result.look == "night"


def test_director_loop_prompt_is_ambient():
    r = compose_local(WorldState(), "ambient", loop=True)
    assert "loop" in r.generation_prompt.lower()


def test_director_substitutes_unsafe():
    assert compose_local(WorldState(), "explicit sex in the market").substituted


def test_kling_jwt_shape():
    assert kling_jwt("ak", "sk").count(".") == 2


def test_formats_resolve_even_dims():
    assert resolve_dims("landscape", "720") == (1280, 720)
    assert resolve_dims("portrait", "720") == (720, 1280)
    assert resolve_dims("square", "540") == (540, 540)
    w, h = resolve_dims("portrait", "1080")
    assert (w, h) == (1080, 1920)


def test_audience_gift_tiers_and_map():
    eng = AudienceEngine()
    d = eng.ingest(AudienceEvent("tiktok", "gift", "mira", gift_name="Rose", gift_value=1))
    assert d and d.trigger == "gift" and d.priority == "queue" and "petals" in d.prompt and "mira" in d.prompt
    d2 = eng.ingest(AudienceEvent("tiktok", "gift", "kenji", gift_name="Lion", gift_value=29999))
    assert d2 and d2.priority == "interrupt" and d2.meta["tier"] == "large"
    assert eng.snapshot()["top_gifters"][0]["user"] == "kenji"


def test_audience_chat_vote_picks_most_repeated():
    eng = AudienceEngine()
    for u, t in [("a", "!scene night falls"), ("b", "make it night please"), ("c", "!scene a storm rolls in"), ("d", "lol")]:
        assert eng.ingest(AudienceEvent("twitch", "chat", u, t)) is None
    import time as _t
    _t.sleep(0.25)
    d = eng.tick()
    assert d and d.trigger == "chat_vote"
    assert "night" in d.prompt and d.meta["votes"] == 2


def test_audience_filters_unsafe_chat():
    eng = AudienceEngine()
    eng.ingest(AudienceEvent("sim", "chat", "x", "!scene explicit sex"))
    assert eng.snapshot()["vote_candidates"] == 0


@pytest.mark.asyncio
async def test_pipeline_end_to_end_mock():
    tl = Timeline()
    await tl.bootstrap()
    assert tl.buffer_ahead() > 0 and tl.active_loop is not None
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)

    assert tl.submit("night falls over the market")["ok"]
    await tl._pump()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)

    assert tl.pending
    tpl, fin, _ = tl.pending[0]
    assert fin.frag_path.exists() and fin.last_frame.exists()
    head = fin.frag_path.read_bytes()
    assert (b"moof" in head) if settings.codec == "h264" else head[:4] == b"\x1a\x45\xdf\xa3"
    anchor = Path(tl.loops[tl.active_loop].anchor_frame)
    assert tpl.conditioning_frame.endswith(anchor.name)

    tl.started -= settings.target_buffer_sec
    tl._fill()
    assert any(s.source == "generated" for s in tl.published) and not tl.pending
    assert tl.metrics().jobs_failed == 0


@pytest.mark.asyncio
async def test_gift_interrupt_goes_first_and_is_labeled():
    tl = Timeline()
    await tl.bootstrap()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    tl.limiter.min_interval_sec = 0
    tl.submit("a storm rolls in")
    tl.ingest_audience(AudienceEvent("tiktok", "gift", "kenji", gift_name="Lion", gift_value=29999))
    assert tl.queue[0]["trigger"] == "gift"
    await tl._pump()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    jobs = [j for j in tl.jobs if j.trigger == "gift"]
    assert jobs and jobs[0].status == "complete" and jobs[0].requested_by == "kenji"
    assert tl.pending and tl.pending[0][0].title.startswith("🎁 kenji")


@pytest.mark.asyncio
async def test_format_restart_changes_dims_and_session():
    tl = Timeline()
    await tl.bootstrap()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    s0 = tl.session
    await tl.restart("portrait", "540")
    assert tl.session == s0 + 1
    assert (settings.width, settings.height) == (540, 960)
    assert tl.active_loop and tl.buffer_ahead() > 0
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    settings.apply_format("landscape", "720")
    settings.width, settings.height = 480, 270


@pytest.mark.asyncio
async def test_failure_holds_the_shot():
    tl = Timeline()
    await tl.bootstrap()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    tl.fail_next = True
    tl.limiter.min_interval_sec = 0
    tl.submit("a storm rolls in")
    await tl._pump()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    assert tl.jobs[0].status == "failed" or tl.queue
    assert tl.buffer_ahead() > 0 and tl.active_loop is not None


@pytest.mark.asyncio
async def test_hold_loop_is_closed():
    frame = settings.media_dir / "frames" / "plate_test.jpg"
    await media.fit_image(settings.plate_path, frame)
    out = await media.hold_loop_from_frame(frame, settings.media_dir / "tmp" / "hold_test.mp4", duration=2)
    a = settings.media_dir / "tmp" / "a.jpg"
    b = settings.media_dir / "tmp" / "b.jpg"
    await media.first_frame(out, a)
    await media.last_frame(out, b)
    from PIL import Image
    import numpy as np

    da = np.asarray(Image.open(a).convert("L").resize((96, 54)), dtype=float)
    db = np.asarray(Image.open(b).convert("L").resize((96, 54)), dtype=float)
    assert float(np.abs(da - db).mean()) < 4.0
