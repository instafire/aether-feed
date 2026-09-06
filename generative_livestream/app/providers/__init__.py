from .base import GenerationRequest, GenerationResult, VideoProvider
from .mock import MockProvider
from .veo import VeoProvider
from .luma import LumaProvider
from .runway import RunwayProvider
from .kling import KlingProvider

PROVIDERS: dict[str, type[VideoProvider]] = {
    "mock": MockProvider,
    "veo": VeoProvider,
    "luma": LumaProvider,
    "runway": RunwayProvider,
    "kling": KlingProvider,
}


def get_provider(name: str) -> VideoProvider:
    cls = PROVIDERS.get(name, MockProvider)
    return cls()
