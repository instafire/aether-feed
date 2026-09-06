from __future__ import annotations

import os

import httpx

from .base import GenerationRequest, GenerationResult, VideoProvider
from .mock import MockProvider


class RunwayProvider(VideoProvider):
    """Runway Gen-4 / Aleph — dedicated extend from a prior frame."""

    name = "runway"
    endpoint = "https://api.dev.runwayml.com/v1/image_to_video"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        key = os.getenv("RUNWAY_API_KEY")
        if not key:
            fallback = await MockProvider().generate(req)
            fallback.provider = "mock(fallback-from-runway)"
            return fallback

        payload = {
            "promptText": req.prompt,
            "model": "gen4",
            "duration": int(req.duration_sec),
            "ratio": "1280:720",
        }
        if req.condition_image_url:
            payload["promptImage"] = req.condition_image_url
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {key}",
                    "X-Runway-Version": "2024-11-06",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        return GenerationResult(
            provider=self.name,
            asset_url=data.get("id", ""),
            duration_sec=req.duration_sec,
            native_extend=bool(req.condition_image_url),
            raw=data,
        )
