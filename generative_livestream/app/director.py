from __future__ import annotations

import json
import re

import httpx

from .config import settings
from .models import DirectorResult, WorldState, utcnow
from .safety import moderate

DIRECTOR_SYSTEM = """You are the creative director for a continuously running, AI-generated
video stream. You do not generate video yourself — you write the exact
prompt that will be sent to a text-to-video model for the next segment.

You will receive:
- WORLD_STATE: established style, setting, characters, current situation.
- LAST_FRAME_DESCRIPTION: a description of the final frame now playing.
- USER_PROMPT: what the user just typed (or what the live audience voted
  for / a gift spectacle to acknowledge).
- AUDIENCE: energy (calm/medium/high), chat rate, trending words, top
  gifter. Match the energy: calm → slow drifting camera and gentle motion;
  high → livelier motion, more happening in frame, but never a hard cut.
  Never render text or usernames in the video.

Your job:
1. Write one vivid, cinematic prompt (under ~120 words) for the next
   5-10 second segment. It must begin from LAST_FRAME_DESCRIPTION and
   incorporate USER_PROMPT. The video model receives the actual last frame
   as its first frame, so describe motion that starts from that exact image.
2. Prefer continuous camera language over hard cuts, unless the user
   clearly wants a scene change.
3. Reuse established characters' visual descriptions verbatim from
   WORLD_STATE. Never invent a new appearance for an existing character.
4. If USER_PROMPT requests something unsafe, or something a video model
   would reject (real people, copyrighted characters, explicit content),
   do not attempt it. Write a graceful in-world alternative instead and
   flag the substitution.
5. Output an updated WORLD_STATE reflecting what will be true once this
   segment plays. Set "look" to one of: golden, night, storm, dawn.

Respond with strict JSON:
{"generation_prompt": "...", "updated_world_state": {...}, "substituted": false}
"""

_LOOK_KEYS = [
    ("night", re.compile(r"\b(night|stars?|midnight|moon|dark(ness)?|nightfall)\b", re.I)),
    ("storm", re.compile(r"\b(rain|storm|thunder|downpour|wet|tempest|wind)\b", re.I)),
    ("dawn", re.compile(r"\b(dawn|sunrise|morning|daybreak|clears? up|sun returns)\b", re.I)),
    ("golden", re.compile(r"\b(golden hour|sunset|dusk|evening light|warm light)\b", re.I)),
]


def infer_look(text: str, current: str) -> str:
    for name, pat in _LOOK_KEYS:
        if pat.search(text):
            return name
    return current


def compose_local(world: WorldState, user_prompt: str, *, loop: bool = False, audience: dict | None = None) -> DirectorResult:
    safety = moderate(user_prompt)
    substituted = False
    intent = user_prompt.strip()
    if not safety.ok and safety.reason == "policy":
        substituted = True
        intent = "the camera holds; weather and lanterns shift gently in place"

    chars = "; ".join(f"{c.name} ({c.visual_desc})" for c in world.characters)
    hard_cut = bool(re.search(r"\b(cut to|smash cut|hard cut|jump to)\b", intent, re.I))
    continuity = (
        "A hard cut, as requested. "
        if hard_cut
        else "Starting from exactly this frame, with no cut, "
    )
    if loop:
        generation_prompt = (
            f"{world.style_prefix}. {world.setting}. Characters: {chars}. "
            f"Starting from exactly this frame: {world.last_frame_description}. "
            "A slow, ambient, seamlessly looping shot — the camera drifts almost imperceptibly, "
            "lanterns sway, cloud sea rolls, and the shot returns to its starting frame. "
            f"Keep the {world.look} light. No new events, no people entering. 8 seconds."
        )
    else:
        energy = (audience or {}).get("energy", "calm")
        pace = {"calm": "slow, drifting motion", "medium": "gentle but lively motion", "high": "lively, energetic motion with more happening in frame"}.get(energy, "slow, drifting motion")
        generation_prompt = (
            f"{world.style_prefix}. {world.setting}. Characters: {chars}. "
            f"Last frame: {world.last_frame_description}. {continuity}{intent}. "
            f"{world.current_camera_state}, {pace}. Keep the {world.tone} tone. No text or letters anywhere. Photoreal, 8 seconds."
        )
    generation_prompt = generation_prompt[:1000]

    updated = world.model_copy(deep=True)
    updated.updated_at = utcnow()
    if not loop:
        updated.last_established_action = intent
        updated.look = infer_look(intent, world.look)
        updated.last_frame_description = (
            f"the same wide shot after: {intent[:120]}"
            + (f" ({updated.look} light)" if updated.look != world.look else "")
        )
        if updated.look == "night":
            updated.tone = "hushed, wondrous"
        elif updated.look == "storm":
            updated.tone = "moody, weather-beaten"
    return DirectorResult(
        generation_prompt=generation_prompt,
        updated_world_state=updated,
        substituted=substituted,
        look=updated.look,
    )


def _payload(world: WorldState, user_prompt: str, audience: dict | None = None) -> str:
    return json.dumps(
        {
            "WORLD_STATE": world.model_dump(mode="json"),
            "LAST_FRAME_DESCRIPTION": world.last_frame_description,
            "USER_PROMPT": user_prompt,
            "AUDIENCE": audience or {"energy": "calm"},
        }
    )


async def _openai(world: WorldState, user_prompt: str, audience: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.director_model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": DIRECTOR_SYSTEM},
                    {"role": "user", "content": _payload(world, user_prompt, audience)},
                ],
            },
        )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])


async def _gemini(world: WorldState, user_prompt: str, audience: dict | None = None) -> dict:
    model = settings.director_model if settings.director_model.startswith("gemini") else "gemini-2.5-flash"
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": settings.gemini_api_key},
            json={
                "systemInstruction": {"parts": [{"text": DIRECTOR_SYSTEM}]},
                "contents": [{"role": "user", "parts": [{"text": _payload(world, user_prompt, audience)}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
        )
        r.raise_for_status()
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


async def compose(world: WorldState, user_prompt: str, *, loop: bool = False, audience: dict | None = None) -> DirectorResult:
    """LLM director when a key is present; deterministic director otherwise.

    Loop prompts always use the local director — they are formulaic and the
    model shouldn't invent new events for an idle beat.
    """
    if loop:
        return compose_local(world, user_prompt, loop=True)
    backend = settings.director_backend
    if backend == "auto":
        backend = "openai" if settings.openai_api_key else ("gemini" if settings.gemini_api_key else "local")
    if backend == "local":
        return compose_local(world, user_prompt, audience=audience)
    safety = moderate(user_prompt)
    if not safety.ok:
        return compose_local(world, user_prompt, audience=audience)
    try:
        raw = await (_openai if backend == "openai" else _gemini)(world, user_prompt, audience)
        updated = WorldState.model_validate({**world.model_dump(mode="json"), **raw["updated_world_state"]})
        if updated.look not in ("golden", "night", "storm", "dawn"):
            updated.look = infer_look(user_prompt, world.look)
        updated.updated_at = utcnow()
        return DirectorResult(
            generation_prompt=str(raw["generation_prompt"])[:1000],
            updated_world_state=updated,
            substituted=bool(raw.get("substituted")),
            look=updated.look,
        )
    except Exception:
        return compose_local(world, user_prompt, audience=audience)
