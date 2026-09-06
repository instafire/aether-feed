from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

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
    style_prefix: str = "cinematic, 35mm film grain, warm golden-hour color grade"
    setting: str = "a floating market town above the clouds"
    characters: list[Character] = Field(
        default_factory=lambda: [
            Character(
                name="the fox-eared courier",
                visual_desc="orange fur, blue traveling cloak, brass goggles",
            )
        ]
    )
    current_camera_state: str = "slow dolly-in, eye-level"
    last_established_action: str = (
        "the courier is descending a rope bridge toward a market stall"
    )
    last_frame_description: str = (
        "a fox-eared courier in a blue cloak walks a rope bridge toward "
        "lantern-lit stalls over a sea of clouds at golden hour"
    )
    tone: str = "whimsical, adventurous"
    updated_at: datetime = Field(default_factory=utcnow)


class Segment(BaseModel):
    segment_id: str
    source: Literal["idle", "generated"]
    prompt_used: str = ""
    duration_sec: float = 6.4
    title: str = ""
    asset_url: str = ""
    last_frame: str = ""
    conditioning_frame: str | None = None
    model_provider: str = "mock"
    status: Literal["pending", "ready", "failed"] = "ready"
    loop_candidate: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class GenerationJob(BaseModel):
    job_id: str
    trigger: Literal["user_prompt", "loop"]
    user_prompt_raw: str
    director_prompt_final: str = ""
    condition_on_segment: str | None = None
    priority: Literal["interrupt", "queue"] = "queue"
    status: Literal["pending", "running", "complete", "failed"] = "pending"
    retries: int = 0
    provider: str = "mock"
    substituted: bool = False
    error: str | None = None
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class DirectorResult(BaseModel):
    generation_prompt: str
    updated_world_state: WorldState
    substituted: bool = False
    scene_id: str | None = None


class PromptRequest(BaseModel):
    prompt: str
    provider: str | None = None


class EngineSnapshot(BaseModel):
    state: EngineState
    buffer_ahead_sec: float
    now_playing: str
    world: WorldState
    jobs: list[GenerationJob]
    queue: list[str]
    segments_ready: int
