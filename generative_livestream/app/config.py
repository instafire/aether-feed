from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    """Runtime configuration. All values come from the environment."""

    primary_provider: str = field(default_factory=lambda: _env("PRIMARY_PROVIDER", "mock"))
    fallback_provider: str = field(default_factory=lambda: _env("FALLBACK_PROVIDER", "mock"))
    loop_provider: str = field(default_factory=lambda: _env("LOOP_PROVIDER", ""))

    width: int = field(default_factory=lambda: int(_env("VIDEO_WIDTH", "1280")))
    height: int = field(default_factory=lambda: int(_env("VIDEO_HEIGHT", "720")))
    fps: int = field(default_factory=lambda: int(_env("VIDEO_FPS", "24")))
    crf: int = field(default_factory=lambda: int(_env("VIDEO_CRF", "23")))
    # "h264" → fragmented MP4 (every mainstream browser). "vp9" → WebM, for
    # Chromium builds without proprietary codecs (e.g. Playwright's).
    codec: str = field(default_factory=lambda: _env("VIDEO_CODEC", "h264"))

    segment_sec: float = field(default_factory=lambda: float(_env("SEGMENT_SEC", "8")))
    loop_sec: float = field(default_factory=lambda: float(_env("LOOP_SEC", "8")))
    target_buffer_sec: float = field(default_factory=lambda: float(_env("TARGET_BUFFER_SEC", "18")))
    low_buffer_sec: float = field(default_factory=lambda: float(_env("LOW_BUFFER_SEC", "8")))
    crossfade_sec: float = field(default_factory=lambda: float(_env("CROSSFADE_SEC", "0.33")))
    loop_library_size: int = field(default_factory=lambda: int(_env("LOOP_LIBRARY_SIZE", "2")))

    media_dir: Path = field(default_factory=lambda: Path(_env("MEDIA_DIR", "media")).resolve())
    plate_path: Path = field(
        default_factory=lambda: Path(
            _env("PLATE_PATH", "../livestream_player/assets/idle_market.jpg")
        ).resolve()
    )

    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", ""))

    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    veo_model: str = field(default_factory=lambda: _env("VEO_MODEL", "veo-3.1-fast-generate-preview"))
    luma_api_key: str = field(default_factory=lambda: _env("LUMA_API_KEY", ""))
    luma_model: str = field(default_factory=lambda: _env("LUMA_MODEL", "ray-2"))
    runway_api_key: str = field(default_factory=lambda: _env("RUNWAY_API_KEY", ""))
    runway_model: str = field(default_factory=lambda: _env("RUNWAY_MODEL", "gen4_turbo"))
    kling_access_key: str = field(default_factory=lambda: _env("KLING_ACCESS_KEY", ""))
    kling_secret_key: str = field(default_factory=lambda: _env("KLING_SECRET_KEY", ""))
    kling_base_url: str = field(default_factory=lambda: _env("KLING_BASE_URL", "https://api-singapore.klingai.com"))
    kling_model: str = field(default_factory=lambda: _env("KLING_MODEL", "kling-v2-1"))
    fal_key: str = field(default_factory=lambda: _env("FAL_KEY", ""))
    fal_model: str = field(
        default_factory=lambda: _env("FAL_MODEL", "fal-ai/kling-video/v2.1/standard/image-to-video")
    )
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    director_model: str = field(default_factory=lambda: _env("DIRECTOR_MODEL", "gpt-4o-mini"))
    director_backend: str = field(default_factory=lambda: _env("DIRECTOR_BACKEND", "auto"))

    provider_timeout_sec: float = field(default_factory=lambda: float(_env("PROVIDER_TIMEOUT_SEC", "420")))

    # Rough $/second of generated video, used for the cost metric only.
    cost_per_sec: dict[str, float] = field(
        default_factory=lambda: {
            "mock": 0.0,
            "veo": 0.40,
            "luma": 0.20,
            "runway": 0.05,
            "kling": 0.07,
            "fal": 0.07,
        }
    )

    @property
    def mime(self) -> str:
        return 'video/webm; codecs="vp9"' if self.codec == "vp9" else 'video/mp4; codecs="avc1.4d4028"'

    @property
    def ext(self) -> str:
        return ".webm" if self.codec == "vp9" else ".mp4"

    def __post_init__(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("loops", "segments", "frames", "raw", "tmp"):
            (self.media_dir / sub).mkdir(exist_ok=True)


settings = Settings()
