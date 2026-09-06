from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EngineState(str, Enum):
    IDLE_LOOP = "IDLE_LOOP"
    PROMPT_QUEUED = "PROMPT_QUEUED"
    SPLICING = "SPLICING"
    PLAYING_GENERATED = "PLAYING_GENERATED"
    GENERATING_NEW_LOOP = "GENERATING_NEW_LOOP"
    BUFFER_LOW = "BUFFER_LOW"
    ERROR_RECOVERY = "ERROR_RECOVERY"


class Character(BaseModel):
    name: str
    visual_desc: str


class WorldState(BaseModel):
    style_prefix: str = (
        "cinematic, 35mm film grain, warm golden-hour color grade, locked wide shot"
    )
    setting: str = "a floating market town above the clouds, rope bridge to a lantern-lit market"
    characters: list[Character] = Field(
        default_factory=lambda: [
            Character(
                name="the fox-eared courier",
                visual_desc="orange fur, blue traveling cloak, brass goggles",
            )
        ]
    )
    current_camera_state: str = "locked wide, very slow push-in, never cuts"
    last_established_action: str = "lanterns sway over the market walkway at golden hour"
    last_frame_description: str = (
        "wide shot of a floating wooden market above clouds, sun low left, rope bridge centre"
    )
    tone: str = "whimsical, adventurous"
    look: str = "golden"
    updated_at: datetime = Field(default_factory=utcnow)


class Segment(BaseModel):
    """One published entry on the live timeline (spec §6)."""

    seq: int
    segment_id: str
    source: Literal["idle", "hold", "generated"]
    prompt_used: str = ""
    duration_sec: float
    start_offset: float
    title: str = ""
    url: str
    frame_url: str
    conditioning_frame: str | None = None
    model_provider: str = "mock"
    native_extend: bool = False
    loop_candidate: bool = False
    loop_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class LoopEntry(BaseModel):
    loop_id: str
    frag_path: str
    norm_path: str
    anchor_frame: str
    duration_sec: float
    title: str
    source: Literal["idle", "hold"]
    provider: str
    look: str = "golden"


class GenerationJob(BaseModel):
    job_id: str
    trigger: Literal["user_prompt", "chat_vote", "gift", "loop", "library"]
    user_prompt_raw: str
    requested_by: str = "director"
    meta: dict[str, Any] = Field(default_factory=dict)
    director_prompt_final: str = ""
    condition_frame: str | None = None
    priority: Literal["interrupt", "queue"] = "queue"
    status: Literal["pending", "running", "complete", "failed", "rejected"] = "pending"
    retries: int = 0
    provider: str = "mock"
    substituted: bool = False
    error: str | None = None
    latency_ms: int | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class DirectorResult(BaseModel):
    generation_prompt: str
    updated_world_state: WorldState
    substituted: bool = False
    look: str = "golden"


class PromptRequest(BaseModel):
    prompt: str
    provider: str | None = None


class FormatRequest(BaseModel):
    format: str
    size: str | None = None


class AudienceEventIn(BaseModel):
    platform: str = "webhook"
    type: Literal["chat", "gift", "like", "follow", "share", "join"]
    user: str = "viewer"
    text: str = ""
    gift_name: str = ""
    gift_value: int = 0
    count: int = 1


class Metrics(BaseModel):
    buffer_ahead_sec: float
    live_offset_sec: float
    segments_published: int
    jobs_total: int
    jobs_failed: int
    jobs_rejected: int
    avg_latency_ms: dict[str, float]
    success_rate: dict[str, float]
    cost_usd_total: float
    loops_in_library: int
    viewers: int


class EngineSnapshot(BaseModel):
    state: EngineState
    buffer_ahead_sec: float
    live_offset_sec: float
    live_seq: int
    now_playing: str
    world: WorldState
    jobs: list[GenerationJob]
    queue: list[str]
    active_loop: str | None
    providers: dict[str, Any]
    primary_provider: str
    events: list[str]
    session: int = 1
    format: dict[str, Any] = Field(default_factory=dict)
    audience: dict[str, Any] = Field(default_factory=dict)
