from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationRequest:
    prompt: str
    duration_sec: float = 6.4
    condition_image_url: str | None = None
    condition_video_url: str | None = None
    loop: bool = False
    reference_image_urls: list[str] | None = None


@dataclass
class GenerationResult:
    provider: str
    asset_url: str
    duration_sec: float
    native_extend: bool = False
    raw: dict | None = None


class VideoProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def generate(self, req: GenerationRequest) -> GenerationResult:
        """Produce a clip. Prefer native extend when condition_video_url is set."""

    async def health(self) -> bool:
        return True
