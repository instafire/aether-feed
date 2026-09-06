"""Build the offline demo clip set for the static player (no backend).

Runs the same mock renderer + pipeline the engine uses, producing a small
state graph: canonical anchor frames for golden / night / storm, one idle
loop per state, and frame-continuous transition clips between every pair
(plus a same-state 'beat'). Every transition ends exactly on the target
state's anchor frame, so the target loop chains with no visible seam.

    python3 scripts/build_demo.py ../livestream_player/demo
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

os.environ.setdefault("VIDEO_WIDTH", "960")
os.environ.setdefault("VIDEO_HEIGHT", "540")
os.environ.setdefault("VIDEO_CRF", "25")
os.environ.setdefault("SEGMENT_SEC", "6")
os.environ.setdefault("LOOP_SEC", "6")

from app import media  # noqa: E402
from app.config import settings  # noqa: E402

STATES = ["golden", "night", "storm"]
LOOKS = {"golden": media.Look.golden(), "night": media.Look.night(), "storm": media.Look.storm()}
DUR = settings.segment_sec


async def still_clip(frame: Path, dst: Path, seconds: float) -> Path:
    dst = media._container(dst)
    await media.run([
        "-loop", "1", "-framerate", str(settings.fps), "-i", str(frame), "-t", f"{seconds:.3f}",
        "-vf", f"scale={settings.width}:{settings.height},format=yuv420p", *media._encode_flags(), str(dst),
    ])
    return dst


async def settle_to_anchor(beat: Path, anchor: Path, dst: Path) -> Path:
    """Dissolve the last 1.5s of a beat into the target anchor still; total = DUR."""
    still = await still_clip(anchor, dst.with_name(dst.stem + "_still.mp4"), 2.0)
    dst = media._container(dst)
    offset = DUR - 1.5
    await media.run([
        "-i", str(beat), "-i", str(still),
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=dissolve:duration=1.5:offset={offset:.3f},format=yuv420p[v]",
        "-map", "[v]", "-t", f"{DUR:.3f}", *media._encode_flags(), *media._mov(), str(dst),
    ])
    still.unlink(missing_ok=True)
    return dst


async def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(exist_ok=True)

    anchors: dict[str, Path] = {}
    anchors["golden"] = await media.fit_image(settings.plate_path, work / "anchor_golden.jpg")
    for state in ("night", "storm"):
        beat = await media.mock_image_to_video(anchors["golden"], work / f"seed_{state}.mp4", LOOKS["golden"], LOOKS[state], duration=DUR, drift=(0.012, -0.006))
        anchors[state] = await media.last_frame(beat, work / f"anchor_{state}.jpg")

    manifest: dict = {"fps": settings.fps, "width": settings.width, "height": settings.height,
                      "duration_sec": DUR, "states": {}, "transitions": {}, "codec": settings.mime}

    for state in STATES:
        norm = await media.mock_image_to_video(anchors[state], work / f"loop_{state}.mp4", LOOKS[state], LOOKS[state], duration=DUR, loop=True)
        frag = await media.fragment(norm, out_dir / f"loop_{state}{settings.ext}")
        poster = out_dir / f"anchor_{state}.jpg"
        poster.write_bytes(anchors[state].read_bytes())
        manifest["states"][state] = {"loop": frag.name, "anchor": poster.name, "title": f"idle · {state}"}
        print("loop", state)

    for src in STATES:
        for dst_state in STATES:
            key = f"{src}>{dst_state}"
            beat = await media.mock_image_to_video(anchors[src], work / f"beat_{src}_{dst_state}.mp4", LOOKS[src], LOOKS[dst_state], duration=DUR, drift=(0.012, -0.006))
            settled = await settle_to_anchor(beat, anchors[dst_state], work / f"settled_{src}_{dst_state}.mp4")
            frag = await media.fragment(settled, out_dir / f"t_{src}_{dst_state}{settings.ext}")
            manifest["transitions"][key] = frag.name
            print("transition", key)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    total = sum(p.stat().st_size for p in out_dir.iterdir())
    print(f"done: {len(list(out_dir.iterdir()))} files, {total/1e6:.1f} MB")


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "livestream_player" / "demo")
    asyncio.run(main(target))
