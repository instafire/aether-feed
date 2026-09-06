from __future__ import annotations

import re
from pathlib import Path

from ..config import settings
from ..media import Look, mock_image_to_video
from .base import GenerationRequest, GenerationResult, VideoProvider

LOOKS = {"golden": Look.golden, "night": Look.night, "storm": Look.storm, "dawn": Look.dawn}

_KEYS = [
    ("night", re.compile(r"\b(night|stars?|midnight|moon|dark(ness)?|nightfall)\b", re.I)),
    ("storm", re.compile(r"\b(rain|storm|thunder|downpour|wet|tempest|wind)\b", re.I)),
    ("dawn", re.compile(r"\b(dawn|sunrise|morning|daybreak|clear(s|ing)? up|sun returns)\b", re.I)),
    ("golden", re.compile(r"\b(golden hour|sunset|dusk|evening light|warm light)\b", re.I)),
]


def infer_look(prompt: str, current: str) -> str:
    for name, pat in _KEYS:
        if pat.search(prompt):
            return name
    return current


class MockProvider(VideoProvider):
    """A real image-to-video renderer (ffmpeg) with no model behind it.

    It honours the provider contract exactly — prompt + conditioning frame in,
    MP4 out — so the buffer, splice, last-frame, and MSE path are exercised
    end-to-end without a paid API. The camera pushes in, the grade animates
    toward whatever the prompt implies, loops are palindromes.
    """

    name = "mock"
    supports_loop = True
    supports_extend = False

    def __init__(self) -> None:
        self.current_look = "golden"

    async def generate(self, req: GenerationRequest) -> GenerationResult:
        from_name = req.look_hint.get("from") or self.current_look
        to_name = req.look_hint.get("to") or infer_look(req.prompt, from_name)
        from_look = LOOKS.get(from_name, Look.golden)()
        to_look = LOOKS.get(to_name, Look.golden)()
        dst = settings.media_dir / "raw" / f"mock_{req.condition_frame.stem}_{to_name}_{'loop' if req.loop else 'beat'}.mp4"
        drift = (0.0, 0.0) if req.loop else (0.012, -0.006)
        dst = await mock_image_to_video(
            req.condition_frame,
            dst,
            from_look=from_look,
            to_look=to_look if not req.loop else from_look,
            duration=req.duration_sec,
            loop=req.loop,
            drift=drift,
        )
        if not req.loop:
            self.current_look = to_name
        return GenerationResult(
            provider=self.name,
            clip_path=dst,
            native_loop=req.loop,
            ref={"look": to_name if not req.loop else from_name},
        )
