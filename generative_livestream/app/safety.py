from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

UNSAFE_PATTERNS = [
    re.compile(r"\b(child|minor|underage|loli|csam)\b", re.I),
    re.compile(r"\b(nude|porn|explicit sex|nsfw)\b", re.I),
    re.compile(r"\b(kill (him|her|them)|murder|behead|gore)\b", re.I),
    re.compile(r"\b(real (person|celebrity)|deepfake)\b", re.I),
    re.compile(r"\b(nazi|hate speech|racial slur)\b", re.I),
    re.compile(r"\b(copyrighted|mickey mouse|disney|pokemon|marvel)\b", re.I),
]


@dataclass
class SafetyResult:
    ok: bool
    reason: str | None = None


def moderate(text: str) -> SafetyResult:
    t = (text or "").strip()
    if not t:
        return SafetyResult(False, "empty")
    if len(t) > 280:
        return SafetyResult(False, "too_long")
    for pat in UNSAFE_PATTERNS:
        if pat.search(t):
            return SafetyResult(False, "policy")
    return SafetyResult(True)


@dataclass
class RateLimiter:
    min_interval_sec: float = 2.5
    last: dict[str, float] = field(default_factory=dict)

    def allow(self, session_id: str = "default") -> bool:
        now = time.monotonic()
        prev = self.last.get(session_id, 0.0)
        if now - prev < self.min_interval_sec:
            return False
        self.last[session_id] = now
        return True
