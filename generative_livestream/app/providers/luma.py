from __future__ import annotations

import os

import httpx

from .base import GenerationRequest, GenerationResult, VideoProvider
from .mock import MockProvider


class LumaProvider(VideoProvider):
    """Luma Dream Machine / Ray2 — native `loop` flag for idle clips."""

    name = "luma"
    endpoint = "https://api.lumalabs.ai/dream-machine/v1/generations"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        key = os.getenv("LUMA_API_KEY")
        if not key:
            fallback = await MockProvider().generate(req)
            fallback.provider = "mock(fallback-from-luma)"
            return fallback

        payload: dict = {
            "prompt": req.prompt,
            "aspect_ratio": "16:9",
            "loop": bool(req.loop),
        }
        if req.condition_image_url:
            payload["keyframes"] = {
                "frame0": {"type": "image", "url": req.condition_image_url}
            }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        return GenerationResult(
            provider=self.name,
            asset_url=data.get("id", ""),
            duration_sec=req.duration_sec,
            native_extend=False,
            raw=data,
        )
