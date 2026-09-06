from __future__ import annotations

import asyncio
import os

import httpx

from .base import GenerationRequest, GenerationResult, VideoProvider
from .mock import MockProvider


class VeoProvider(VideoProvider):
    """Google Veo 3.1 via Gemini API — scene extension when a prior clip exists."""

    name = "veo"
    model = "veo-3.1-generate-preview"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            fallback = await MockProvider().generate(req)
            fallback.provider = "mock(fallback-from-veo)"
            return fallback

        # Veo long-running predict. Scene extension uses the last second of
        # the prior clip when condition_video_url is present (spec §7).
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:predictLongRunning"
        )
        instance: dict = {"prompt": req.prompt}
        if req.condition_video_url:
            instance["video"] = {"uri": req.condition_video_url}
        elif req.condition_image_url:
            instance["image"] = {"uri": req.condition_image_url}
        payload = {
            "instances": [instance],
            "parameters": {
                "aspectRatio": "16:9",
                "durationSeconds": int(req.duration_sec),
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, params={"key": key}, json=payload)
            r.raise_for_status()
            op = r.json()
            name = op.get("name")
            for _ in range(40):
                await asyncio.sleep(3)
                s = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/{name}",
                    params={"key": key},
                )
                s.raise_for_status()
                body = s.json()
                if body.get("done"):
                    video = (
                        body.get("response", {})
                        .get("generateVideoResponse", {})
                        .get("generatedSamples", [{}])[0]
                        .get("video", {})
                    )
                    uri = video.get("uri") or video.get("uri") or ""
                    return GenerationResult(
                        provider=self.name,
                        asset_url=uri,
                        duration_sec=req.duration_sec,
                        native_extend=bool(req.condition_video_url),
                        raw=body,
                    )
        raise TimeoutError("Veo generation timed out")
