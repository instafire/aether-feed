from __future__ import annotations

import asyncio
import base64
import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import settings


class ProviderError(RuntimeError):
    pass


class ProviderRejected(ProviderError):
    """Content-policy rejection — surface to the user, don't retry blindly."""


@dataclass
class GenerationRequest:
    prompt: str
    duration_sec: float
    condition_frame: Path
    loop: bool = False
    # Native continuation: the prior clip and the provider-specific handle it
    # returned (Veo video uri, Luma generation id, Kling video_id, ...).
    prior_clip: Path | None = None
    prior_ref: dict[str, Any] | None = None
    prior_provider: str | None = None
    reference_frames: list[Path] = field(default_factory=list)
    # Only used by the mock renderer to animate the grade.
    look_hint: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationResult:
    provider: str
    clip_path: Path
    native_extend: bool = False
    native_loop: bool = False
    # Seconds at the head of the clip that duplicate the prior clip (Veo
    # extension returns input+extension; the pipeline trims this off).
    head_overlap_sec: float = 0.0
    ref: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class VideoProvider(ABC):
    name: str = "base"
    supports_loop: bool = False
    supports_extend: bool = False

    @abstractmethod
    async def generate(self, req: GenerationRequest) -> GenerationResult: ...

    def configured(self) -> bool:
        return True


def frame_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def frame_b64(path: Path) -> tuple[str, str]:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return base64.b64encode(path.read_bytes()).decode(), mime


def public_frame_url(path: Path) -> str | None:
    """Providers that only accept image URLs need a public origin (PUBLIC_BASE_URL)."""
    if not settings.public_base_url:
        return None
    return settings.public_base_url.rstrip("/") + "/api/frames/" + path.name


async def download(url: str, dst: Path, headers: dict[str, str] | None = None) -> Path:
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers or {}) as r:
            r.raise_for_status()
            with dst.open("wb") as fh:
                async for chunk in r.aiter_bytes():
                    fh.write(chunk)
    return dst


async def poll(fn, *, interval: float = 5.0, timeout: float | None = None):
    """Call `fn()` until it returns a non-None value or the timeout passes."""
    limit = timeout or settings.provider_timeout_sec
    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        out = await fn()
        if out is not None:
            return out
        if loop.time() - start > limit:
            raise ProviderError("generation timed out")
        await asyncio.sleep(interval)
