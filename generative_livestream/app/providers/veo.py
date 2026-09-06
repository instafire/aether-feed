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
    frame_b64,
    poll,
)

BASE = "https://generativelanguage.googleapis.com/v1beta"


class VeoProvider(VideoProvider):
    """Google Veo 3.1 via the Gemini API.

    - Beat: image-to-video conditioned on the last frame.
    - Native extend: if the prior clip came from Veo, extend it by 7s and trim
      the duplicated head (the API returns input + extension).
    - Loop: first frame == last frame interpolation.
    """

    name = "veo"
    supports_loop = True
    supports_extend = True

    def configured(self) -> bool:
        return bool(settings.gemini_api_key)

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not self.configured():
            raise ProviderError("GEMINI_API_KEY not set")
        b64, mime = frame_b64(req.condition_frame)
        instance: dict = {"prompt": req.prompt}
        params: dict = {"aspectRatio": "9:16" if settings.format == "portrait" else "16:9", "resolution": "720p", "numberOfVideos": 1}
        native_extend = False
        overlap = 0.0

        prior_uri = (req.prior_ref or {}).get("video_uri") if req.prior_provider == "veo" else None
        if prior_uri and not req.loop:
            instance["video"] = {"uri": prior_uri}
            native_extend = True
            overlap = float((req.prior_ref or {}).get("duration_sec") or 0.0)
            params["durationSeconds"] = "8"
        else:
            instance["image"] = {"inlineData": {"mimeType": mime, "data": b64}}
            if req.loop:
                instance["lastFrame"] = {"inlineData": {"mimeType": mime, "data": b64}}
            dur = 8 if req.duration_sec > 6 else (6 if req.duration_sec > 4 else 4)
            params["durationSeconds"] = str(dur)

        url = f"{BASE}/models/{settings.veo_model}:predictLongRunning"
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, headers=self._headers(), json={"instances": [instance], "parameters": params})
            if r.status_code == 400 and "safety" in r.text.lower():
                raise ProviderRejected(r.text[:300])
            r.raise_for_status()
            op_name = r.json()["name"]

            async def check():
                s = await client.get(f"{BASE}/{op_name}", headers=self._headers())
                s.raise_for_status()
                body = s.json()
                if body.get("error"):
                    msg = str(body["error"])
                    if "safety" in msg.lower() or "policy" in msg.lower():
                        raise ProviderRejected(msg[:300])
                    raise ProviderError(msg[:300])
                if body.get("done"):
                    return body
                return None

            body = await poll(check, interval=8)

        resp = body.get("response", {}).get("generateVideoResponse", {})
        samples = resp.get("generatedSamples") or []
        if not samples:
            filtered = resp.get("raiMediaFilteredReasons") or []
            raise ProviderRejected(f"Veo returned no video: {filtered[:1]}")
        video = samples[0]["video"]
        uri = video["uri"]
        dst = settings.media_dir / "raw" / f"veo_{req.condition_frame.stem}.mp4"
        await download(uri, dst, headers={"x-goog-api-key": settings.gemini_api_key})
        return GenerationResult(
            provider=self.name,
            clip_path=dst,
            native_extend=native_extend,
            native_loop=req.loop,
            head_overlap_sec=overlap,
            ref={"video_uri": uri},
            raw=body,
        )
