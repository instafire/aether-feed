from __future__ import annotations

import asyncio

from ..config import settings
from .base import GenerationRequest, GenerationResult, ProviderError, VideoProvider, download, frame_data_uri


class FalProvider(VideoProvider):
    """Any fal.ai image-to-video endpoint (default: Kling 2.1 via fal).

    Set FAL_KEY and optionally FAL_MODEL. Expects the endpoint to accept
    `prompt` + `image_url` and return `{"video": {"url": ...}}`.
    """

    name = "fal"
    supports_loop = False
    supports_extend = False

    def configured(self) -> bool:
        return bool(settings.fal_key)

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not self.configured():
            raise ProviderError("FAL_KEY not set")
        try:
            import fal_client
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("pip install fal-client") from exc

        import os

        os.environ.setdefault("FAL_KEY", settings.fal_key)
        args = {
            "prompt": req.prompt[:1500],
            "image_url": frame_data_uri(req.condition_frame),
            "duration": "10" if req.duration_sec > 7 else "5",
            "aspect_ratio": "16:9",
        }
        result = await asyncio.to_thread(fal_client.subscribe, settings.fal_model, arguments=args)
        video = (result or {}).get("video") or {}
        url = video.get("url")
        if not url:
            raise ProviderError(f"fal returned no video: {str(result)[:200]}")
        dst = settings.media_dir / "raw" / f"fal_{req.condition_frame.stem}.mp4"
        await download(url, dst)
        return GenerationResult(provider=self.name, clip_path=dst, raw=result)
