"""Audience sources — platform connectors that feed AudienceEngine.

All connectors are optional and start only when their env vars are set:

- Twitch: anonymous IRC over WebSocket (official, read-only). Bits arrive as
  `bits=` tags on PRIVMSG; subs/raids as USERNOTICE.
- YouTube Live: Data API v3 liveChatMessages polling (API key + video id).
  Super Chats/Stickers carry amountMicros.
- TikTok LIVE: there is no public gift API. If the community `TikTokLive`
  package is installed, we connect to the webcast feed for chat, gifts,
  likes, follows, shares. Otherwise TikTok events can be relayed through the
  generic webhook (`POST /api/audience/event`) from any tool you run.
- Simulator: synthetic chat/gifts for development.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Awaitable, Callable

import httpx

from .audience import AudienceEvent
from .config import settings

log = logging.getLogger("livestream.audience")
Emit = Callable[[AudienceEvent], Awaitable[None] | None]


async def _emit(emit: Emit, ev: AudienceEvent) -> None:
    out = emit(ev)
    if asyncio.iscoroutine(out):
        await out


# ------------------------------------------------------------------ Twitch
_TAG_RE = re.compile(r"^@([^ ]+) ")


async def twitch_irc(channel: str, emit: Emit) -> None:
    """Read Twitch chat anonymously. Reconnects forever."""
    try:
        import websockets  # provided by uvicorn[standard]
    except ImportError:  # pragma: no cover
        log.warning("websockets not installed; Twitch source disabled")
        return
    nick = f"justinfan{random.randint(10000, 99999)}"
    while True:
        try:
            async with websockets.connect("wss://irc-ws.chat.twitch.tv:443") as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send(f"NICK {nick}")
                await ws.send(f"JOIN #{channel.lower()}")
                log.info("twitch: joined #%s", channel)
                async for raw in ws:
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        tags: dict[str, str] = {}
                        m = _TAG_RE.match(line)
                        if m:
                            tags = dict(kv.split("=", 1) for kv in m.group(1).split(";") if "=" in kv)
                            line = line[m.end():]
                        user = tags.get("display-name") or (line.split("!")[0].lstrip(":") if "!" in line else "viewer")
                        if " PRIVMSG " in line:
                            text = line.split(" :", 1)[1] if " :" in line else ""
                            bits = int(tags.get("bits") or 0)
                            if bits:
                                await _emit(emit, AudienceEvent("twitch", "gift", user, text, gift_name="cheer", gift_value=bits))
                            else:
                                await _emit(emit, AudienceEvent("twitch", "chat", user, text))
                        elif " USERNOTICE " in line:
                            kind = tags.get("msg-id", "")
                            if kind in ("sub", "resub", "subgift", "anonsubgift"):
                                await _emit(emit, AudienceEvent("twitch", "gift", user, gift_name="sub", gift_value=500))
                            elif kind == "raid":
                                await _emit(emit, AudienceEvent("twitch", "share", user, count=int(tags.get("msg-param-viewerCount") or 1)))
        except Exception as exc:
            log.warning("twitch: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)


# ----------------------------------------------------------------- YouTube
async def youtube_live_chat(api_key: str, video_id: str, emit: Emit) -> None:
    base = "https://www.googleapis.com/youtube/v3"
    chat_id = None
    page = None
    async with httpx.AsyncClient(timeout=20) as client:
        while chat_id is None:
            try:
                r = await client.get(f"{base}/videos", params={"part": "liveStreamingDetails", "id": video_id, "key": api_key})
                r.raise_for_status()
                items = r.json().get("items") or []
                chat_id = (items[0].get("liveStreamingDetails") or {}).get("activeLiveChatId") if items else None
                if not chat_id:
                    log.warning("youtube: no active live chat yet; retrying")
                    await asyncio.sleep(15)
            except Exception as exc:
                log.warning("youtube: %s", exc)
                await asyncio.sleep(15)
        log.info("youtube: reading live chat %s", chat_id)
        while True:
            try:
                params = {"part": "snippet,authorDetails", "liveChatId": chat_id, "key": api_key, "maxResults": 200}
                if page:
                    params["pageToken"] = page
                r = await client.get(f"{base}/liveChat/messages", params=params)
                r.raise_for_status()
                body = r.json()
                page = body.get("nextPageToken")
                for item in body.get("items", []):
                    sn = item["snippet"]
                    user = item.get("authorDetails", {}).get("displayName", "viewer")
                    kind = sn.get("type")
                    if kind in ("superChatEvent", "superStickerEvent"):
                        det = sn.get("superChatDetails") or sn.get("superStickerDetails") or {}
                        cents = int(int(det.get("amountMicros") or 0) / 10000)
                        await _emit(emit, AudienceEvent("youtube", "gift", user, det.get("userComment", ""), gift_name="superchat", gift_value=cents))
                    elif kind == "newSponsorEvent":
                        await _emit(emit, AudienceEvent("youtube", "gift", user, gift_name="member", gift_value=500))
                    elif kind == "textMessageEvent":
                        await _emit(emit, AudienceEvent("youtube", "chat", user, sn.get("displayMessage", "")))
                await asyncio.sleep(max(2.0, body.get("pollingIntervalMillis", 5000) / 1000))
            except Exception as exc:
                log.warning("youtube: %s", exc)
                await asyncio.sleep(10)


# ------------------------------------------------------------------ TikTok
async def tiktok_live(unique_id: str, emit: Emit) -> None:
    try:
        from TikTokLive import TikTokLiveClient  # type: ignore
        from TikTokLive.events import CommentEvent, FollowEvent, GiftEvent, LikeEvent, ShareEvent  # type: ignore
    except ImportError:
        log.warning("TikTokLive not installed (pip install TikTokLive); relay TikTok events via POST /api/audience/event")
        return
    loop = asyncio.get_running_loop()
    client = TikTokLiveClient(unique_id=unique_id)

    def _schedule(ev: AudienceEvent) -> None:
        loop.create_task(_emit(emit, ev))

    @client.on(CommentEvent)
    async def _on_comment(e):  # noqa: ANN001
        _schedule(AudienceEvent("tiktok", "chat", getattr(e.user, "nickname", "viewer"), e.comment))

    @client.on(GiftEvent)
    async def _on_gift(e):  # noqa: ANN001
        # Streakable gifts fire repeatedly while the streak is open; count once at the end.
        if getattr(e.gift, "streakable", False) and getattr(e, "streaking", False):
            return
        _schedule(AudienceEvent(
            "tiktok", "gift", getattr(e.user, "nickname", "viewer"),
            gift_name=getattr(e.gift, "name", "gift"),
            gift_value=int(getattr(e.gift, "diamond_count", 1) or 1),
            count=int(getattr(e, "repeat_count", 1) or 1),
        ))

    @client.on(LikeEvent)
    async def _on_like(e):  # noqa: ANN001
        _schedule(AudienceEvent("tiktok", "like", getattr(e.user, "nickname", "viewer"), count=int(getattr(e, "count", 1) or 1)))

    @client.on(FollowEvent)
    async def _on_follow(e):  # noqa: ANN001
        _schedule(AudienceEvent("tiktok", "follow", getattr(e.user, "nickname", "viewer")))

    @client.on(ShareEvent)
    async def _on_share(e):  # noqa: ANN001
        _schedule(AudienceEvent("tiktok", "share", getattr(e.user, "nickname", "viewer")))

    while True:
        try:
            log.info("tiktok: connecting to @%s", unique_id)
            await client.start()
        except Exception as exc:
            log.warning("tiktok: %s — retrying in 20s", exc)
            await asyncio.sleep(20)


# --------------------------------------------------------------- simulator
_SIM_USERS = ["mira", "kenji", "ada", "lou", "priya", "theo", "sol", "nia", "rafa", "june"]
_SIM_CHAT = [
    "this is so pretty", "night mode pls", "!scene a storm rolls in", "make it night", "lol the lanterns",
    "!scene the sun comes back", "can we see rain", "dragon when", "so calm", "!scene night falls over the market",
    "wow", "storm storm storm", "hi from brazil", "!scene fireworks over the market", "morning light please",
]
_SIM_GIFTS = [("Rose", 1), ("Heart", 1), ("TikTok", 1), ("Galaxy", 1000), ("Lion", 29999), ("cheer", 100), ("superchat", 499)]


async def simulator(emit: Emit, *, rate_per_min: float = 24, gift_every_sec: float = 25) -> None:
    last_gift = asyncio.get_running_loop().time()
    while True:
        await asyncio.sleep(max(0.5, random.expovariate(rate_per_min / 60.0)))
        user = random.choice(_SIM_USERS)
        now = asyncio.get_running_loop().time()
        if now - last_gift > gift_every_sec:
            last_gift = now
            name, value = random.choices(_SIM_GIFTS, weights=[40, 30, 20, 4, 1, 3, 2])[0]
            await _emit(emit, AudienceEvent("sim", "gift", user, gift_name=name, gift_value=value))
        else:
            kind = random.random()
            if kind < 0.08:
                await _emit(emit, AudienceEvent("sim", "like", user, count=random.randint(1, 15)))
            elif kind < 0.11:
                await _emit(emit, AudienceEvent("sim", "follow", user))
            else:
                await _emit(emit, AudienceEvent("sim", "chat", user, random.choice(_SIM_CHAT)))
