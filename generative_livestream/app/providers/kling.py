from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

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


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def kling_jwt(access_key: str, secret_key: str, ttl: int = 1800) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url(json.dumps({"iss": access_key, "exp": now + ttl, "nbf": now - 5}).encode())
    signing = f"{header}.{payload}".encode()
    sig = hmac.new(secret_key.encode(), signing, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


class KlingProvider(VideoProvider):
    """Kling image-to-video with native video extension chaining."""

    name = "kling"
    supports_loop = False
    supports_extend = True

    def configured(self) -> bool:
        return bool(settings.kling_access_key and settings.kling_secret_key)

    def _headers(self) -> dict[str, str]:
        token = kling_jwt(settings.kling_access_key, settings.kling_secret_key)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        if not self.configured():
            raise ProviderError("KLING_ACCESS_KEY / KLING_SECRET_KEY not set")
        base = settings.kling_base_url.rstrip("/")
        prior_video = (req.prior_ref or {}).get("video_id") if req.prior_provider == "kling" else None
        native_extend = False
        if prior_video and not req.loop:
            path = "/v1/videos/video-extend"
            payload = {"video_id": prior_video, "prompt": req.prompt[:2500]}
            native_extend = True
        else:
            b64, _ = frame_b64(req.condition_frame)
            path = "/v1/videos/image2video"
            payload = {
                "model_name": settings.kling_model,
                "image": b64,
                "prompt": req.prompt[:2500],
                "mode": "std",
                "aspect_ratio": settings.aspect_ratio,
                "duration": "10" if req.duration_sec > 7 else "5",
                "cfg_scale": 0.5,
            }

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(base + path, headers=self._headers(), json=payload)
            r.raise_for_status()
            body = r.json()
            if body.get("code") not in (0, None):
                msg = str(body.get("message"))
                if "risk" in msg.lower() or "sensitive" in msg.lower():
                    raise ProviderRejected(msg[:300])
                raise ProviderError(msg[:300])
            task_id = body["data"]["task_id"]

            async def check():
                s = await client.get(f"{base}{path}/{task_id}", headers=self._headers())
                s.raise_for_status()
                data = s.json().get("data", {})
                status = data.get("task_status")
                if status == "succeed":
                    return data
                if status == "failed":
                    msg = str(data.get("task_status_msg") or "failed")
                    if "risk" in msg.lower() or "sensitive" in msg.lower():
                        raise ProviderRejected(msg[:300])
                    raise ProviderError(msg[:300])
                return None

            data = await poll(check, interval=6)

        videos = (data.get("task_result") or {}).get("videos") or []
        if not videos:
            raise ProviderError("Kling succeeded without video")
        v = videos[0]
        dst = settings.media_dir / "raw" / f"kling_{task_id}.mp4"
        await download(v["url"], dst)
        return GenerationResult(
            provider=self.name,
            clip_path=dst,
            native_extend=native_extend,
            ref={"video_id": v.get("id"), "task_id": task_id},
            raw=data,
        )
