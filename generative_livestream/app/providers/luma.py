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
    poll,
    public_frame_url,
)

BASE = "https://api.lumalabs.ai/dream-machine/v1"


class LumaProvider(VideoProvider):
    """Luma Dream Machine (Ray 2).

    - Native `loop: true` for idle loops (spec §5.5 option 1).
    - Extend: keyframe frame0 = prior Luma generation id.
    - Otherwise: keyframe frame0 = last-frame image (needs PUBLIC_BASE_URL —
      Luma only accepts image URLs).
    """

    name = "luma"
    supports_loop = True
    supports_extend = True

    def configured(self) -> bool:
        return bool(settings.luma_api_key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.luma_api_key}", "Content-Type": "application/json"}

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not self.configured():
            raise ProviderError("LUMA_API_KEY not set")
        payload: dict = {
            "prompt": req.prompt,
            "model": settings.luma_model,
            "aspect_ratio": settings.aspect_ratio,
            "resolution": "720p",
            "duration": "9s" if req.duration_sec > 6 else "5s",
            "loop": bool(req.loop),
        }
        native_extend = False
        prior_id = (req.prior_ref or {}).get("generation_id") if req.prior_provider == "luma" else None
        if prior_id:
            payload["keyframes"] = {"frame0": {"type": "generation", "id": prior_id}}
            native_extend = True
        else:
            url = public_frame_url(req.condition_frame)
            if not url:
                raise ProviderError(
                    "Luma needs a public image URL for frame conditioning — set PUBLIC_BASE_URL"
                )
            payload["keyframes"] = {"frame0": {"type": "image", "url": url}}

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{BASE}/generations", headers=self._headers(), json=payload)
            r.raise_for_status()
            gen = r.json()
            gen_id = gen["id"]

            async def check():
                s = await client.get(f"{BASE}/generations/{gen_id}", headers=self._headers())
                s.raise_for_status()
                body = s.json()
                state = body.get("state")
                if state == "completed":
                    return body
                if state == "failed":
                    reason = str(body.get("failure_reason") or "failed")
                    if "moderation" in reason.lower() or "policy" in reason.lower():
                        raise ProviderRejected(reason[:300])
                    raise ProviderError(reason[:300])
                return None

            body = await poll(check, interval=6)

        video_url = (body.get("assets") or {}).get("video")
        if not video_url:
            raise ProviderError("Luma completed without a video asset")
        dst = settings.media_dir / "raw" / f"luma_{gen_id}.mp4"
        await download(video_url, dst)
        return GenerationResult(
            provider=self.name,
            clip_path=dst,
            native_extend=native_extend,
            native_loop=req.loop,
            ref={"generation_id": gen_id},
            raw=body,
        )
