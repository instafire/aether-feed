from .base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ProviderRejected,
    VideoProvider,
)
from .fal import FalProvider
from .kling import KlingProvider
from .luma import LumaProvider
from .mock import MockProvider
from .runway import RunwayProvider
from .veo import VeoProvider

PROVIDERS: dict[str, type[VideoProvider]] = {
    "mock": MockProvider,
    "veo": VeoProvider,
    "luma": LumaProvider,
    "runway": RunwayProvider,
    "kling": KlingProvider,
    "fal": FalProvider,
}

_instances: dict[str, VideoProvider] = {}


def get_provider(name: str) -> VideoProvider:
    """One instance per provider (the mock keeps grade state between calls)."""
    key = name if name in PROVIDERS else "mock"
    if key not in _instances:
        _instances[key] = PROVIDERS[key]()
    return _instances[key]


def provider_status() -> dict[str, dict]:
    out = {}
    for name, cls in PROVIDERS.items():
        p = get_provider(name)
        out[name] = {
            "configured": p.configured(),
            "supports_loop": cls.supports_loop,
            "supports_extend": cls.supports_extend,
        }
    return out


__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "ProviderError",
    "ProviderRejected",
    "VideoProvider",
    "get_provider",
    "provider_status",
    "PROVIDERS",
]
