from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


# Output formats the stream can run in. Size tiers are the short edge.
FORMATS: dict[str, dict] = {
    "landscape": {"label": "Landscape 16:9 (YouTube / Twitch)", "ratio": "16:9", "w": 16, "h": 9},
    "portrait": {"label": "Portrait 9:16 (TikTok LIVE / Reels / Shorts)", "ratio": "9:16", "w": 9, "h": 16},
    "square": {"label": "Square 1:1 (Instagram)", "ratio": "1:1", "w": 1, "h": 1},
}
SIZES = {"540": 540, "720": 720, "1080": 1080}


def resolve_dims(fmt: str, size: str) -> tuple[int, int]:
    f = FORMATS.get(fmt, FORMATS["landscape"])
    short = SIZES.get(size, 720)
    if f["w"] >= f["h"]:
        h = short
        w = int(round(short * f["w"] / f["h"] / 2) * 2)
    else:
        w = short
        h = int(round(short * f["h"] / f["w"] / 2) * 2)
    return w, h


DEFAULT_GIFT_MAP = {
    "rose": "a drift of rose petals crosses the frame on the wind",
    "heart": "paper lanterns rise gently into the sky",
    "tiktok": "a ripple of light runs along every lantern in the market",
    "galaxy": "an aurora unfurls across the sky above the market",
    "lion": "a great amber dragon glides across the bridge",
    "universe": "the whole sky blooms into a slow supernova of light while the market glows",
    "cheer": "fireworks bloom silently over the cloud sea",
    "superchat": "a comet arcs slowly over the market",
}


@dataclass
class Settings:
    """Runtime configuration. Values come from the environment; format and
    size can be changed at runtime through `apply_format`."""

    primary_provider: str = field(default_factory=lambda: _env("PRIMARY_PROVIDER", "mock"))
    fallback_provider: str = field(default_factory=lambda: _env("FALLBACK_PROVIDER", "mock"))
    loop_provider: str = field(default_factory=lambda: _env("LOOP_PROVIDER", ""))

    format: str = field(default_factory=lambda: _env("STREAM_FORMAT", "landscape"))
    size: str = field(default_factory=lambda: _env("STREAM_SIZE", "720"))
    width: int = 1280
    height: int = 720
    fps: int = field(default_factory=lambda: int(_env("VIDEO_FPS", "24")))
    crf: int = field(default_factory=lambda: int(_env("VIDEO_CRF", "23")))
    codec: str = field(default_factory=lambda: _env("VIDEO_CODEC", "h264"))

    segment_sec: float = field(default_factory=lambda: float(_env("SEGMENT_SEC", "8")))
    loop_sec: float = field(default_factory=lambda: float(_env("LOOP_SEC", "8")))
    target_buffer_sec: float = field(default_factory=lambda: float(_env("TARGET_BUFFER_SEC", "18")))
    low_buffer_sec: float = field(default_factory=lambda: float(_env("LOW_BUFFER_SEC", "8")))
    crossfade_sec: float = field(default_factory=lambda: float(_env("CROSSFADE_SEC", "0.33")))
    loop_library_size: int = field(default_factory=lambda: int(_env("LOOP_LIBRARY_SIZE", "2")))

    media_dir: Path = field(default_factory=lambda: Path(_env("MEDIA_DIR", "media")).resolve())
    plate_landscape: Path = field(
        default_factory=lambda: Path(_env("PLATE_PATH", "../livestream_player/assets/idle_market.jpg")).resolve()
    )
    plate_portrait: Path = field(
        default_factory=lambda: Path(
            _env("PLATE_PORTRAIT_PATH", "../livestream_player/assets/idle_market_portrait.jpg")
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

    # Audience engine
    chat_window_sec: float = field(default_factory=lambda: float(_env("CHAT_WINDOW_SEC", "20")))
    chat_min_votes: int = field(default_factory=lambda: int(_env("CHAT_MIN_VOTES", "1")))
    chat_command_prefix: str = field(default_factory=lambda: _env("CHAT_COMMAND_PREFIX", "!scene"))
    gift_tier_medium: int = field(default_factory=lambda: int(_env("GIFT_TIER_MEDIUM", "100")))
    gift_tier_large: int = field(default_factory=lambda: int(_env("GIFT_TIER_LARGE", "1000")))
    gift_cooldown_sec: float = field(default_factory=lambda: float(_env("GIFT_COOLDOWN_SEC", "12")))
    gift_map: dict[str, str] = field(default_factory=lambda: {**DEFAULT_GIFT_MAP, **json.loads(_env("GIFT_MAP_JSON", "{}"))})
    tiktok_unique_id: str = field(default_factory=lambda: _env("TIKTOK_UNIQUE_ID", ""))
    twitch_channel: str = field(default_factory=lambda: _env("TWITCH_CHANNEL", ""))
    youtube_api_key: str = field(default_factory=lambda: _env("YOUTUBE_API_KEY", ""))
    youtube_video_id: str = field(default_factory=lambda: _env("YOUTUBE_VIDEO_ID", ""))

    cost_per_sec: dict[str, float] = field(
        default_factory=lambda: {"mock": 0.0, "veo": 0.40, "luma": 0.20, "runway": 0.05, "kling": 0.07, "fal": 0.07}
    )

    @property
    def mime(self) -> str:
        return 'video/webm; codecs="vp9"' if self.codec == "vp9" else 'video/mp4; codecs="avc1.4d4028"'

    @property
    def ext(self) -> str:
        return ".webm" if self.codec == "vp9" else ".mp4"

    @property
    def aspect_ratio(self) -> str:
        return FORMATS.get(self.format, FORMATS["landscape"])["ratio"]

    @property
    def plate_path(self) -> Path:
        if self.format == "portrait" and self.plate_portrait.exists():
            return self.plate_portrait
        return self.plate_landscape

    def apply_format(self, fmt: str, size: str | None = None) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"unknown format {fmt}")
        if size is not None:
            if size not in SIZES:
                raise ValueError(f"unknown size {size}")
            self.size = size
        self.format = fmt
        self.width, self.height = resolve_dims(self.format, self.size)

    def format_info(self) -> dict:
        return {
            "format": self.format,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "codec": self.mime,
            "formats": {k: v["label"] for k, v in FORMATS.items()},
            "sizes": list(SIZES.keys()),
        }

    def __post_init__(self) -> None:
        self.apply_format(self.format if self.format in FORMATS else "landscape", self.size if self.size in SIZES else "720")
        if os.getenv("VIDEO_WIDTH") and os.getenv("VIDEO_HEIGHT"):
            self.width, self.height = int(os.environ["VIDEO_WIDTH"]), int(os.environ["VIDEO_HEIGHT"])
        self.media_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("loops", "segments", "frames", "raw", "tmp"):
            (self.media_dir / sub).mkdir(exist_ok=True)


settings = Settings()
