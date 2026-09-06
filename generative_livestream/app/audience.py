"""Audience engine: turns chat and gifts into stream direction (spec §10).

Ingests normalized events from any platform (TikTok LIVE, Twitch, YouTube,
webhook, simulator) and decides *when* and *what* to generate:

- Gifts: mapped to in-world spectacles by name and tier. Large gifts
  interrupt the prompt queue; small ones queue. Per-gift cooldown.
- Chat: `!scene <direction>` commands (or any direction-like message) are
  collected into a voting window; the most-repeated direction wins and is
  submitted once per window. Everything passes moderation first.
- Hype: rolling chat rate + gift coins/min → an energy hint for the director.
"""

from __future__ import annotations

import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Literal

from .config import settings
from .safety import moderate

EventType = Literal["chat", "gift", "like", "follow", "share", "join"]

_STOP = set("""a an the and or but of to in on at for with from by as is are be it its this that these those
i you we they he she me my your our their so very just like really please pls plz can could would should make
do does did lol omg wow yes no ok okay hi hello hey thanks thank ty gg""".split())
_DIRECTION_HINT = re.compile(
    r"\b(night|day|dawn|sunrise|sunset|rain|storm|snow|fog|wind|fire|dragon|bird|boat|ship|lantern|"
    r"festival|market|sky|cloud|moon|star|sun|light|dark|golden|aurora|comet|firework|petal|flower|"
    r"camera|pan|zoom|fly|flyover|walk|arrive|appear|land|open|close|glow|shine|falls?|rolls? in)\b",
    re.I,
)


@dataclass
class AudienceEvent:
    platform: str
    type: EventType
    user: str = "viewer"
    text: str = ""
    gift_name: str = ""
    gift_value: int = 0  # coins / bits / cents
    count: int = 1
    ts: float = field(default_factory=time.time)


@dataclass
class Direction:
    """What the audience engine asks the timeline to generate."""

    prompt: str
    trigger: Literal["chat_vote", "gift"]
    priority: Literal["interrupt", "queue"]
    user: str
    meta: dict


class AudienceEngine:
    def __init__(self, on_direction: Callable[[Direction], None] | None = None) -> None:
        self.on_direction = on_direction
        self.recent: deque[AudienceEvent] = deque(maxlen=400)
        self.chat_rate: deque[float] = deque(maxlen=600)
        self.gift_log: deque[tuple[float, int, str]] = deque(maxlen=600)
        self.likes: int = 0
        self.follows: int = 0
        self.shares: int = 0
        self.gifters: Counter[str] = Counter()
        self.candidates: list[tuple[str, str, str]] = []  # (normalized, original, user)
        self.window_started = time.monotonic()
        self.last_gift_direction = 0.0
        self.last_vote: dict | None = None
        self.log: deque[str] = deque(maxlen=60)
        self.sources: dict[str, str] = {}

    # ---------------------------------------------------------------- ingest
    def ingest(self, ev: AudienceEvent) -> Direction | None:
        self.recent.append(ev)
        if ev.type == "chat":
            self.chat_rate.append(time.monotonic())
            return self._on_chat(ev)
        if ev.type == "gift":
            return self._on_gift(ev)
        if ev.type == "like":
            self.likes += max(1, ev.count)
        elif ev.type == "follow":
            self.follows += 1
        elif ev.type == "share":
            self.shares += 1
        return None

    def _normalize(self, text: str) -> str:
        """Key on direction keywords when present so 'night falls' and
        'make it night please' land in the same bucket."""
        hints = sorted({m.group(0).lower() for m in _DIRECTION_HINT.finditer(text)})
        if hints:
            return " ".join(hints)
        words = [w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOP]
        return " ".join(sorted(set(words)))

    @staticmethod
    def _cluster(keys: list[str]) -> dict[str, list[str]]:
        """Greedy clustering: a key joins the first cluster it shares a word with."""
        clusters: dict[str, set[str]] = {}
        members: dict[str, list[str]] = {}
        for k in keys:
            ws = set(k.split())
            home = None
            for rep, cws in clusters.items():
                if ws & cws:
                    home = rep
                    break
            if home is None:
                clusters[k] = set(ws)
                members[k] = [k]
            else:
                clusters[home] |= ws
                members[home].append(k)
        return members

    def _on_chat(self, ev: AudienceEvent) -> Direction | None:
        text = ev.text.strip()
        prefix = settings.chat_command_prefix.lower()
        is_cmd = text.lower().startswith(prefix)
        if is_cmd:
            text = text[len(prefix):].strip(" :")
        if not text:
            return None
        if not is_cmd and not _DIRECTION_HINT.search(text):
            return None  # plain chatter — counts toward hype only
        if not moderate(text).ok:
            self.log.appendleft(f"chat from {ev.user} filtered")
            return None
        key = self._normalize(text)
        if not key:
            return None
        self.candidates.append((key, text[:140], ev.user))
        return None

    def _gift_tier(self, value: int) -> str:
        if value >= settings.gift_tier_large:
            return "large"
        if value >= settings.gift_tier_medium:
            return "medium"
        return "small"

    def _on_gift(self, ev: AudienceEvent) -> Direction | None:
        total = max(1, ev.gift_value) * max(1, ev.count)
        self.gift_log.append((time.monotonic(), total, ev.user))
        self.gifters[ev.user] += total
        tier = self._gift_tier(total)
        name = (ev.gift_name or "").lower().strip()
        spectacle = None
        for key, phrase in settings.gift_map.items():
            if key in name:
                spectacle = phrase
                break
        if spectacle is None:
            spectacle = {
                "small": "a single lantern flares brighter for a moment",
                "medium": "a flock of glowing paper birds sweeps across the sky",
                "large": "the sky ignites with slow golden fireworks over the whole market",
            }[tier]
        now = time.monotonic()
        if tier == "small" and now - self.last_gift_direction < settings.gift_cooldown_sec:
            self.log.appendleft(f"{ev.user} sent {ev.gift_name or 'a gift'} (cooldown, folded into hype)")
            return None
        self.last_gift_direction = now
        prompt = f"{spectacle} — a thank-you to {ev.user}"
        self.log.appendleft(f"{ev.user} sent {ev.gift_name or 'a gift'} x{ev.count} → {tier} spectacle")
        return Direction(
            prompt=prompt,
            trigger="gift",
            priority="interrupt" if tier == "large" else "queue",
            user=ev.user,
            meta={"tier": tier, "gift": ev.gift_name, "value": total, "platform": ev.platform},
        )

    # ----------------------------------------------------------------- votes
    def tick(self) -> Direction | None:
        """Close the chat window if due and return the winning direction."""
        if time.monotonic() - self.window_started < settings.chat_window_sec:
            return None
        self.window_started = time.monotonic()
        if not self.candidates:
            return None
        members = self._cluster([k for k, _, _ in self.candidates])
        rep, keys = max(members.items(), key=lambda kv: len(kv[1]))
        votes = len(keys)
        if votes < settings.chat_min_votes:
            self.candidates.clear()
            return None
        keyset = set(keys)
        # Most common literal phrasing in the winning cluster becomes the prompt.
        phr = Counter(o for k, o, _ in self.candidates if k in keyset)
        original = phr.most_common(1)[0][0]
        voters = sorted({u for k, _, u in self.candidates if k in keyset})
        self.candidates.clear()
        self.last_vote = {"prompt": original, "votes": votes, "voters": voters[:5]}
        self.log.appendleft(f"chat vote: '{original}' ({votes})")
        return Direction(
            prompt=original,
            trigger="chat_vote",
            priority="queue",
            user=voters[0] if voters else "chat",
            meta={"votes": votes, "voters": voters[:5]},
        )

    # ----------------------------------------------------------------- state
    def chat_per_min(self) -> float:
        cutoff = time.monotonic() - 60
        return float(sum(1 for t in self.chat_rate if t > cutoff))

    def coins_per_min(self) -> int:
        cutoff = time.monotonic() - 60
        return int(sum(v for t, v, _ in self.gift_log if t > cutoff))

    def hype(self) -> float:
        """0..1 energy score from chat rate and gifts."""
        c = min(1.0, self.chat_per_min() / 60.0)
        g = min(1.0, self.coins_per_min() / float(settings.gift_tier_large))
        return round(min(1.0, 0.6 * c + 0.6 * g), 2)

    def energy_label(self) -> str:
        h = self.hype()
        return "high" if h > 0.6 else ("medium" if h > 0.25 else "calm")

    def trending(self, n: int = 5) -> list[str]:
        cutoff = time.time() - 120
        words: Counter[str] = Counter()
        for ev in self.recent:
            if ev.type == "chat" and ev.ts > cutoff:
                for w in re.findall(r"[a-z']+", ev.text.lower()):
                    if w not in _STOP and len(w) > 2:
                        words[w] += 1
        return [w for w, _ in words.most_common(n)]

    def snapshot(self) -> dict:
        window_left = max(0.0, settings.chat_window_sec - (time.monotonic() - self.window_started))
        top = None
        if self.candidates:
            members = self._cluster([k for k, _, _ in self.candidates])
            rep, keys = max(members.items(), key=lambda kv: len(kv[1]))
            keyset = set(keys)
            phr = Counter(o for k, o, _ in self.candidates if k in keyset)
            top = {"prompt": phr.most_common(1)[0][0], "votes": len(keys)}
        return {
            "chat_per_min": self.chat_per_min(),
            "coins_per_min": self.coins_per_min(),
            "hype": self.hype(),
            "energy": self.energy_label(),
            "likes": self.likes,
            "follows": self.follows,
            "shares": self.shares,
            "top_gifters": [{"user": u, "coins": c} for u, c in self.gifters.most_common(3)],
            "trending": self.trending(),
            "vote_window_sec": round(window_left, 1),
            "vote_leader": top,
            "vote_candidates": len(self.candidates),
            "last_vote": self.last_vote,
            "sources": self.sources,
            "recent": [
                {"platform": e.platform, "type": e.type, "user": e.user, "text": e.text,
                 "gift_name": e.gift_name, "gift_value": e.gift_value, "count": e.count, "ts": e.ts}
                for e in list(self.recent)[-30:]
            ],
            "log": list(self.log)[:10],
        }

    def director_context(self) -> dict:
        return {
            "energy": self.energy_label(),
            "chat_per_min": self.chat_per_min(),
            "trending": self.trending(4),
            "top_gifter": (self.gifters.most_common(1) or [("", 0)])[0][0],
        }
