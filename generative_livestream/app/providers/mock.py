from __future__ import annotations

import asyncio
import random

from .base import GenerationRequest, GenerationResult, VideoProvider

ASSETS = {
    "idle": [
        "/assets/idle_market.jpg",
        "/assets/idle_courier.jpg",
        "/assets/idle_clouds.jpg",
    ],
    "generated": [
        "/assets/scene_dragon.jpg",
        "/assets/scene_night.jpg",
        "/assets/scene_rain.jpg",
    ],
}


class MockProvider(VideoProvider):
    name = "mock"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        await asyncio.sleep(0.05)
        bucket = "idle" if req.loop else "generated"
        url = random.choice(ASSETS[bucket])
        prompt = req.prompt.lower()
        if "dragon" in prompt:
            url = "/assets/scene_dragon.jpg"
        elif "night" in prompt or "star" in prompt:
            url = "/assets/scene_night.jpg"
        elif "rain" in prompt or "storm" in prompt:
            url = "/assets/scene_rain.jpg"
        return GenerationResult(
            provider=self.name,
            asset_url=url,
            duration_sec=req.duration_sec,
            native_extend=bool(req.condition_video_url),
        )
