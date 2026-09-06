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
os.environ.setdefault("MEDIA_DIR", str(Path(__file__).resolve().parent / "_media"))

from app import media  # noqa: E402
from app.config import settings  # noqa: E402
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
    result = compose_local(world, "night falls over the market")
    assert "orange fur, blue traveling cloak, brass goggles" in result.generation_prompt
    assert "Starting from exactly this frame" in result.generation_prompt
    assert result.look == "night"
    assert result.updated_world_state.look == "night"


def test_director_loop_prompt_is_ambient():
    r = compose_local(WorldState(), "ambient", loop=True)
    assert "loop" in r.generation_prompt.lower()
    assert r.updated_world_state.look == "golden"


def test_director_substitutes_unsafe():
    r = compose_local(WorldState(), "explicit sex in the market")
    assert r.substituted


def test_kling_jwt_shape():
    tok = kling_jwt("ak", "sk")
    assert tok.count(".") == 2


@pytest.mark.asyncio
async def test_pipeline_end_to_end_mock():
    """Bootstrap → prompt → beat spliced → frame-continuous → loop anchored."""
    tl = Timeline()
    await tl.bootstrap()
    assert tl.buffer_ahead() > 0
    assert tl.active_loop is not None

    # let the library job finish
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)

    ok = tl.submit("night falls over the market")
    assert ok["ok"]
    await tl._pump()
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)
    # loop job spawned from inside the prompt job
    await asyncio.gather(*list(tl._inflight), return_exceptions=True)

    assert tl.pending, "beat should be waiting to splice"
    tpl, fin, hold_id = tl.pending[0]
    assert fin.frag_path.exists() and fin.last_frame.exists()
    head = fin.frag_path.read_bytes()
    assert (b"moof" in head) if settings.codec == "h264" else head[:4] == b"\x1a\x45\xdf\xa3"

    # Beat must be conditioned on the active loop's anchor frame.
    anchor = Path(tl.loops[tl.active_loop].anchor_frame)
    assert tpl.conditioning_frame.endswith(anchor.name)

    # Force the splice by moving the clock forward past the buffer.
    tl.started -= settings.target_buffer_sec
    tl._fill()
    assert any(s.source == "generated" for s in tl.published)
    assert not tl.pending
    # After the beat, the fill continues from a loop anchored to the beat.
    after = [s for s in tl.published if s.start_offset > 0][-1]
    assert after.source in ("hold", "idle")
    jobs = {j.trigger: j.status for j in tl.jobs}
    assert jobs.get("user_prompt") == "complete"
    assert tl.metrics().jobs_failed == 0


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
    # first attempt failed and was requeued once
    assert tl.jobs[0].status == "failed" or tl.queue
    assert tl.buffer_ahead() > 0
    assert tl.active_loop is not None


@pytest.mark.asyncio
async def test_hold_loop_is_closed():
    frame = settings.media_dir / "frames" / "plate.jpg"
    if not frame.exists():
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
