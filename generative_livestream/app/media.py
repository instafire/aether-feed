"""FFmpeg media operations: normalize, fragment for MSE, last-frame extraction,
seam crossfade, palindrome hold loops, and the mock image-to-video renderer.

Every provider's output passes through `normalize` so the stream has one
codec, size, fps, and color space regardless of who generated it (spec §5.3).
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import settings


def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("ffmpeg not found; install ffmpeg or imageio-ffmpeg") from exc


FFMPEG = ffmpeg_bin()

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


class MediaError(RuntimeError):
    pass


def _run(args: list[str], timeout: float = 600) -> str:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise MediaError(proc.stderr.strip()[-2000:] or f"ffmpeg failed: {' '.join(args)}")
    return proc.stderr


async def run(args: list[str], timeout: float = 600) -> str:
    return await asyncio.to_thread(_run, args, timeout)


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    m = _DURATION_RE.search(proc.stderr)
    if not m:
        raise MediaError(f"could not read duration of {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _encode_flags() -> list[str]:
    if settings.codec == "vp9":
        return [
            "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
            "-crf", str(min(settings.crf + 8, 45)), "-b:v", "0", "-pix_fmt", "yuv420p",
            "-g", str(settings.fps), "-keyint_min", str(settings.fps), "-an",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(settings.crf),
        "-profile:v", "main",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
        "-g", str(settings.fps),
        "-keyint_min", str(settings.fps),
        "-sc_threshold", "0",
        "-an",
    ]


def _scale_chain() -> str:
    w, h = settings.width, settings.height
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={settings.fps},format=yuv420p"
    )


def _container(dst: Path) -> Path:
    return dst.with_suffix(".webm") if settings.codec == "vp9" else dst


def _mov() -> list[str]:
    return [] if settings.codec == "vp9" else ["-movflags", "+faststart"]


async def normalize(src: Path, dst: Path, max_duration: float | None = None) -> Path:
    """Re-encode any provider clip to the stream's single codec profile."""
    dst = _container(dst)
    args: list[str] = ["-i", str(src)]
    if max_duration:
        args += ["-t", f"{max_duration:.3f}"]
    args += ["-vf", _scale_chain(), *_encode_flags(), *_mov(), str(dst)]
    await run(args)
    return dst


async def trim_head(src: Path, dst: Path, start_sec: float) -> Path:
    """Drop the first `start_sec` seconds (used for Veo extension output)."""
    dst = _container(dst)
    await run(["-ss", f"{start_sec:.3f}", "-i", str(src), "-vf", _scale_chain(), *_encode_flags(), str(dst)])
    return dst


async def fragment(src: Path, dst: Path) -> Path:
    """Remux to a streamable container the browser can append to an MSE SourceBuffer."""
    if settings.codec == "vp9":
        dst = dst.with_suffix(".webm")
        await run(["-i", str(src), "-c", "copy", "-an", "-f", "webm", "-cluster_time_limit", "1000", str(dst)])
        return dst
    await run([
        "-i", str(src),
        "-c", "copy", "-an",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-frag_duration", "1000000",
        str(dst),
    ])
    return dst


async def last_frame(src: Path, dst: Path) -> Path:
    """Extract the final frame — the conditioning image for the next generation."""
    dur = await asyncio.to_thread(probe_duration, src)
    t = max(0.0, dur - 1.5 / settings.fps)
    await run(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)])
    if not dst.exists():
        await run(["-sseof", "-0.1", "-i", str(src), "-update", "1", "-frames:v", "1", "-q:v", "2", str(dst)])
    return dst


async def fit_image(src: Path, dst: Path) -> Path:
    """Scale/crop a still to the stream frame size."""
    w, h = settings.width, settings.height
    await run(["-i", str(src), "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", "-frames:v", "1", "-q:v", "2", str(dst)])
    return dst


async def first_frame(src: Path, dst: Path) -> Path:
    await run(["-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst)])
    return dst


async def crossfade_head(prev: Path, nxt: Path, dst: Path, duration: float | None = None) -> Path:
    """Bake a short crossfade from the tail of `prev` into the head of `nxt` (spec §9).

    Output length equals `nxt`'s length; the first `duration` seconds blend the
    two clips so small lighting/grain mismatches at the splice disappear.
    """
    d = duration or settings.crossfade_sec
    tail = d + 0.02
    dst = _container(dst)
    await run([
        "-sseof", f"-{tail:.3f}", "-i", str(prev),
        "-i", str(nxt),
        "-filter_complex",
        f"[0:v]fps={settings.fps},format=yuv420p,setpts=PTS-STARTPTS[a];"
        f"[1:v]fps={settings.fps},format=yuv420p,setpts=PTS-STARTPTS[b];"
        f"[a][b]xfade=transition=fade:duration={d:.3f}:offset=0,format=yuv420p[v]",
        "-map", "[v]",
        *_encode_flags(),
        *_mov(),
        str(dst),
    ])
    return dst


def _zoompan(z_expr: str, x_drift: float, y_drift: float, frames: int) -> str:
    w, h = settings.width, settings.height
    n = max(1, frames)
    x = f"iw/2-(iw/zoom/2)+({x_drift})*iw*on/{n}"
    y = f"ih/2-(ih/zoom/2)+({y_drift})*ih*on/{n}"
    return (
        f"scale=-2:2160,zoompan=z='{z_expr}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={settings.fps}"
    )


async def hold_loop_from_frame(frame: Path, dst: Path, duration: float | None = None) -> Path:
    """Palindrome micro-zoom from a still. First frame == last frame == `frame`.

    This is the never-blank fallback: when a generated beat ends and the
    provider's new loop isn't back yet, the tail 'breathes' on the last frame
    instead of freezing or going black (spec FR7, §5.4 GENERATING_NEW_LOOP).
    """
    dur = duration or settings.loop_sec
    frames = int(round(dur * settings.fps))
    z = f"1+0.018*sin(PI*on/{frames})"
    vf = _zoompan(z, 0.0, 0.0, frames) + ",format=yuv420p"
    dst = _container(dst)
    await run([
        "-loop", "1", "-framerate", str(settings.fps), "-i", str(frame),
        "-t", f"{dur:.3f}", "-vf", vf, *_encode_flags(), *_mov(), str(dst),
    ])
    return dst


@dataclass
class Look:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma_b: float = 1.0
    gamma_r: float = 1.0
    hue: float = 0.0
    noise: int = 0

    @staticmethod
    def golden() -> "Look":
        return Look()

    @staticmethod
    def night() -> "Look":
        return Look(brightness=-0.30, contrast=1.10, saturation=0.72, gamma_b=1.25, gamma_r=0.9, hue=8)

    @staticmethod
    def storm() -> "Look":
        return Look(brightness=-0.20, contrast=0.92, saturation=0.5, gamma_b=1.10, gamma_r=0.94, hue=-6)

    @staticmethod
    def dawn() -> "Look":
        return Look(brightness=0.04, contrast=1.02, saturation=1.10, gamma_r=1.06, hue=-4)


def _lerp_expr(a: float, b: float, dur: float) -> str:
    # Quoted so the comma in min() doesn't split the filter option list.
    return f"'{a:.4f}+({b - a:.4f})*min(t/{dur:.3f}\\,1)'"


async def mock_image_to_video(
    frame: Path,
    dst: Path,
    from_look: Look,
    to_look: Look,
    duration: float | None = None,
    loop: bool = False,
    drift: tuple[float, float] = (0.0, 0.0),
) -> Path:
    """Mock provider renderer: image-to-video with a graded, drifting camera.

    - `loop=True` makes a palindrome (first == last frame) anchored on `frame`.
    - Otherwise the camera pushes in ~3% and the grade animates from
      `from_look` (already baked into `frame`) toward `to_look`.
    """
    dur = duration or settings.segment_sec
    frames = int(round(dur * settings.fps))
    if loop:
        z = f"1+0.02*sin(PI*on/{frames})"
        eq = ""
        hue = ""
        noise = f",noise=alls={to_look.noise}:allf=t" if to_look.noise else ""
    else:
        z = f"1+0.03*on/{frames}"
        d = to_look
        f = from_look
        eq = (
            ",eq=brightness=" + _lerp_expr(0.0, d.brightness - f.brightness, dur)
            + ":contrast=" + _lerp_expr(1.0, d.contrast / max(f.contrast, 0.1), dur)
            + ":saturation=" + _lerp_expr(1.0, d.saturation / max(f.saturation, 0.1), dur)
            + ":gamma_b=" + _lerp_expr(1.0, d.gamma_b / max(f.gamma_b, 0.1), dur)
            + ":gamma_r=" + _lerp_expr(1.0, d.gamma_r / max(f.gamma_r, 0.1), dur)
        )
        hue = ",hue=h=" + _lerp_expr(0.0, d.hue - f.hue, dur)
        noise = f",noise=alls={d.noise}:allf=t" if d.noise else ""
    vf = _zoompan(z, drift[0], drift[1], frames) + eq + hue + noise + ",format=yuv420p"
    dst = _container(dst)
    await run([
        "-loop", "1", "-framerate", str(settings.fps), "-i", str(frame),
        "-t", f"{dur:.3f}", "-vf", vf, *_encode_flags(), *_mov(), str(dst),
    ])
    return dst
