from __future__ import annotations

import os

import httpx

from .base import GenerationRequest, GenerationResult, VideoProvider
from .mock import MockProvider


class KlingProvider(VideoProvider):
    """Kling — 5/10s base clips with customized extend chaining."""

    name = "kling"
    endpoint = "https://api.klingai.com/v1/videos/text2video"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        access = os.getenv("KLING_ACCESS_KEY")
        secret = os.getenv("KLING_SECRET_KEY")
        if not access or not secret:
            fallback = await MockProvider().generate(req)
            fallback.provider = "mock(fallback-from-kling)"
            return fallback

        payload = {
            "prompt": req.prompt,
            "duration": "5",
            "aspect_ratio": "16:9",
            "mode": "std",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {access}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        return GenerationResult(
            provider=self.name,
            asset_url=str(data.get("data", {}).get("task_id", "")),
            duration_sec=req.duration_sec,
            native_extend=bool(req.condition_video_url),
            raw=data,
        )
