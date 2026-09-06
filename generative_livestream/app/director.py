from __future__ import annotations

import json
import os
import re
from typing import Any

from .models import DirectorResult, WorldState
from .safety import moderate

DIRECTOR_SYSTEM = """You are the creative director for a continuously running, AI-generated
video stream. You do not generate video yourself — you write the exact
prompt that will be sent to a text-to-video model for the next segment.

You will receive:
- WORLD_STATE: established style, setting, characters, current situation.
- LAST_FRAME_DESCRIPTION: a description of the final frame now playing.
- USER_PROMPT: what the user just typed.

Your job:
1. Write one vivid, cinematic prompt (under ~120 words) for the next
   5-10 second segment. It must begin from LAST_FRAME_DESCRIPTION and
   incorporate USER_PROMPT.
2. Prefer continuous camera language over hard cuts, unless the user
   clearly wants a scene change.
3. Reuse established characters' visual descriptions verbatim from
   WORLD_STATE. Never invent a new appearance for an existing character.
4. If USER_PROMPT requests something unsafe, or something a video model
   would reject (real people, copyrighted characters, explicit content),
   do not attempt it. Write a graceful in-world alternative instead and
   flag the substitution.
5. Output an updated WORLD_STATE reflecting what will be true once this
   segment plays.

Respond with strict JSON:
{"generation_prompt": "...", "updated_world_state": {...}, "substituted": false}
"""

SCENE_BANK = [
    {
        "id": "scene_dragon",
        "keys": ["dragon", "wyrm", "lands", "landing", "beast", "wings"],
        "last_frame": (
            "an amber-scaled dragon lands on the rope bridge as the "
            "fox-eared courier watches from the near deck"
        ),
        "patch": {
            "last_established_action": (
                "an amber dragon has just landed on the market bridge; the courier watches"
            ),
            "current_camera_state": "low three-quarter, slight push-in",
        },
    },
    {
        "id": "scene_night",
        "keys": ["night", "star", "dusk", "evening", "dark", "moon", "twilight"],
        "last_frame": (
            "the floating market at night, paper lanterns glowing under a star-filled sky"
        ),
        "patch": {
            "last_established_action": "night has fallen over the floating market",
            "current_camera_state": "high wide, slow aerial drift",
            "tone": "hushed, wondrous",
        },
    },
    {
        "id": "scene_rain",
        "keys": ["rain", "storm", "thunder", "wet", "wind", "downpour"],
        "last_frame": (
            "rain sheets across wet wooden market boards, lanterns flickering "
            "above a gulf of cloud"
        ),
        "patch": {
            "last_established_action": "a storm has rolled in over the market walkways",
            "current_camera_state": "eye-level tracking along the wet boards",
            "tone": "moody, weather-beaten",
        },
    },
]


def match_scene(prompt: str) -> dict[str, Any] | None:
    p = prompt.lower()
    best = None
    score = 0
    for scene in SCENE_BANK:
        n = sum(1 for k in scene["keys"] if k in p)
        if n > score:
            score = n
            best = scene
    return best


def compose_local(world: WorldState, user_prompt: str) -> DirectorResult:
    safety = moderate(user_prompt)
    substituted = False
    intent = user_prompt.strip()
    if not safety.ok and safety.reason == "policy":
        substituted = True
        intent = (
            "the camera holds on the current scene as weather and lanterns "
            "shift, staying in-world"
        )

    scene = match_scene(intent)
    hard_cut = bool(re.search(r"cut|smash cut|hard cut|jump to", intent, re.I))
    continuity = (
        "hard cut as requested, then "
        if hard_cut
        else "continuing from the last frame, the camera eases forward as "
    )
    chars = "; ".join(f"{c.name} ({c.visual_desc})" for c in world.characters)
    generation_prompt = (
        f"{world.style_prefix}. Setting: {world.setting}. Characters: {chars}. "
        f"Last frame: {world.last_frame_description}. {continuity}{intent}. "
        f"Keep {world.tone} tone. {world.current_camera_state}. "
        "Under 8 seconds, photoreal cinematic, no text, no watermark."
    )[:900]

    updated = world.model_copy(deep=True)
    updated.last_established_action = intent
    from .models import utcnow

    updated.updated_at = utcnow()
    if scene:
        for k, v in scene["patch"].items():
            setattr(updated, k, v)
        updated.last_frame_description = scene["last_frame"]

    return DirectorResult(
        generation_prompt=generation_prompt,
        updated_world_state=updated,
        substituted=substituted,
        scene_id=scene["id"] if scene else None,
    )


async def compose(world: WorldState, user_prompt: str) -> DirectorResult:
    """Prefer a live LLM if OPENAI_API_KEY is set; otherwise deterministic director."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return compose_local(world, user_prompt)
    try:
        import httpx

        payload = {
            "model": os.getenv("DIRECTOR_MODEL", "gpt-4o-mini"),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": DIRECTOR_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "WORLD_STATE": world.model_dump(mode="json"),
                            "LAST_FRAME_DESCRIPTION": world.last_frame_description,
                            "USER_PROMPT": user_prompt,
                        }
                    ),
                },
            ],
        }
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            raw = json.loads(data["choices"][0]["message"]["content"])
        updated = WorldState.model_validate(raw["updated_world_state"])
        return DirectorResult(
            generation_prompt=raw["generation_prompt"],
            updated_world_state=updated,
            substituted=bool(raw.get("substituted")),
        )
    except Exception:
        return compose_local(world, user_prompt)
