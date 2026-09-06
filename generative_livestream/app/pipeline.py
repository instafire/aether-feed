"""Post-generation pipeline: provider clip → normalized → seam-smoothed →
fragmented for MSE → last frame extracted. One entry point, `finish_clip`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from . import media
from .config import settings
from .providers.base import GenerationResult


@dataclass
class FinishedClip:
    clip_id: str
    norm_path: Path
    frag_path: Path
    last_frame: Path
    duration_sec: float


async def finish_clip(
    result: GenerationResult,
    *,
    prev_norm: Path | None,
    is_loop: bool,
    target_duration: float,
) -> FinishedClip:
    clip_id = f"{result.provider}_{uuid.uuid4().hex[:8]}"
    tmp = settings.media_dir / "tmp"
    seg_dir = settings.media_dir / ("loops" if is_loop else "segments")

    src = result.clip_path
    if result.head_overlap_sec > 0.2:
        # Veo extension returns input + extension; keep only the new part.
        trimmed = tmp / f"{clip_id}_trim.mp4"
        await media.trim_head(src, trimmed, result.head_overlap_sec)
        src = trimmed

    norm = await media.normalize(src, seg_dir / f"{clip_id}.mp4", max_duration=target_duration if not is_loop else None)

    # Loops are anchored to their own first frame, so a crossfade at the head
    # would only blur the loop point; apply the seam blend to beats only.
    if prev_norm is not None and not is_loop and not result.native_extend and settings.crossfade_sec > 0:
        try:
            blended = await media.crossfade_head(prev_norm, norm, seg_dir / f"{clip_id}_x.mp4")
            norm.unlink(missing_ok=True)
            norm = blended
        except media.MediaError:
            pass

    frag = await media.fragment(norm, seg_dir / f"{clip_id}.frag.mp4")
    frame = settings.media_dir / "frames" / f"{clip_id}.jpg"
    await media.last_frame(norm, frame)
    duration = media.probe_duration(norm)
    return FinishedClip(clip_id=clip_id, norm_path=norm, frag_path=frag, last_frame=frame, duration_sec=duration)


async def make_hold_loop(anchor_frame: Path) -> FinishedClip:
    """Local palindrome 'breathing' loop from a still — the never-blank fallback."""
    clip_id = f"hold_{uuid.uuid4().hex[:8]}"
    norm = await media.hold_loop_from_frame(anchor_frame, settings.media_dir / "loops" / f"{clip_id}.mp4")
    frag = await media.fragment(norm, settings.media_dir / "loops" / f"{clip_id}.frag.mp4")
    frame = settings.media_dir / "frames" / f"{clip_id}.jpg"
    await media.last_frame(norm, frame)
    return FinishedClip(clip_id=clip_id, norm_path=norm, frag_path=frag, last_frame=frame, duration_sec=media.probe_duration(norm))
