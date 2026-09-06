from __future__ import annotations

import asyncio

import pytest

from app.buffer import CRITICAL_BUFFER, TARGET_BUFFER, BufferManager
from app.director import compose_local
from app.models import WorldState
from app.safety import moderate


def test_moderate_blocks_policy():
    assert moderate("a dragon lands").ok
    assert not moderate("explicit sex scene").ok
    assert not moderate("").ok


def test_director_keeps_character_verbatim():
    world = WorldState()
    result = compose_local(world, "a dragon lands on the bridge")
    prompt = result.generation_prompt
    assert "orange fur, blue traveling cloak, brass goggles" in prompt
    assert "continuing from the last frame" in prompt
    assert result.scene_id == "scene_dragon"
    assert result.updated_world_state.characters[0].visual_desc == world.characters[0].visual_desc


def test_director_substitutes_unsafe():
    result = compose_local(WorldState(), "explicit sex in the market")
    assert result.substituted
    assert "staying in-world" in result.generation_prompt


def test_buffer_never_blank_over_simulated_minutes():
    mgr = BufferManager()
    assert mgr.current is not None
    assert mgr.buffer_ahead() > 0
    # 8 minutes of playback at 50ms ticks, no generation
    dt = 0.05
    steps = int(8 * 60 / dt)
    min_buf = mgr.buffer_ahead()
    blanks = 0
    for _ in range(steps):
        mgr.tick(dt)
        if mgr.current is None:
            blanks += 1
        min_buf = min(min_buf, mgr.buffer_ahead())
    assert blanks == 0
    assert min_buf > 0
    assert mgr.buffer_ahead() >= CRITICAL_BUFFER


def test_prompt_queues_and_splices():
    mgr = BufferManager()
    ok = mgr.submit("a dragon lands on the bridge")
    assert ok["ok"]
    assert mgr.queue

    async def run():
        await mgr.pump()
        await asyncio.sleep(0.2)
        await asyncio.gather(*list(mgr._inflight), return_exceptions=True)

    asyncio.run(run())
    assert any(s.source == "generated" for s in mgr.segments) or mgr.state.value in {
        "SPLICING",
        "PROMPT_QUEUED",
        "PLAYING_GENERATED",
        "ERROR_RECOVERY",
    }


def test_failure_extends_tail():
    mgr = BufferManager()
    before = len(mgr.segments)
    mgr.fail_next = True

    async def run():
        mgr.submit("rain sheets across the boards")
        await mgr.pump()
        await asyncio.gather(*list(mgr._inflight), return_exceptions=True)

    asyncio.run(run())
    assert mgr.buffer_ahead() > 0
    assert len(mgr.segments) >= before
