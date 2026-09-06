from __future__ import annotations

import httpx

from ..config import settings
from .base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderRejected,
    VideoProvider,
    download,
    frame_data_uri,
    poll,
)

BASE = "https://api.dev.runwayml.com/v1"
VERSION = "2024-11-06"


class RunwayProvider(VideoProvider):
    """Runway Gen-4 Turbo image-to-video, task-based async API.

    Accepts a data-URI conditioning image, so no public origin is needed.
    """

    name = "runway"
    supports_loop = False
    supports_extend = False

    def configured(self) -> bool:
        return bool(settings.runway_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.runway_api_key}",
            "X-Runway-Version": VERSION,
            "Content-Type": "application/json",
        }

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not self.configured():
            raise ProviderError("RUNWAY_API_KEY not set")
        payload = {
            "model": settings.runway_model,
            "promptImage": frame_data_uri(req.condition_frame),
            "promptText": req.prompt[:1000],
            "duration": 10 if req.duration_sec > 7 else 5,
            "ratio": "1280:720",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{BASE}/image_to_video", headers=self._headers(), json=payload)
            if r.status_code == 400 and "moderation" in r.text.lower():
                raise ProviderRejected(r.text[:300])
            r.raise_for_status()
            task_id = r.json()["id"]

            async def check():
                s = await client.get(f"{BASE}/tasks/{task_id}", headers=self._headers())
                s.raise_for_status()
                body = s.json()
                status = body.get("status")
                if status == "SUCCEEDED":
                    return body
                if status in ("FAILED", "CANCELLED"):
                    reason = str(body.get("failure") or body.get("failureCode") or status)
                    if "SAFETY" in reason.upper() or "MODERATION" in reason.upper():
                        raise ProviderRejected(reason[:300])
                    raise ProviderError(reason[:300])
                return None

            body = await poll(check, interval=5)

        outputs = body.get("output") or []
        if not outputs:
            raise ProviderError("Runway task succeeded without output")
        dst = settings.media_dir / "raw" / f"runway_{task_id}.mp4"
        await download(outputs[0], dst)
        return GenerationResult(provider=self.name, clip_path=dst, ref={"task_id": task_id}, raw=body)
