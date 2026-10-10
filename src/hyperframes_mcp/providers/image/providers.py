"""Image providers: the client protocol, a registry, and the settings-driven factory.

A provider is a name, the environment variable holding its API key, and a function that builds
its client from the configured ``ImageSettings``. ``openai`` and ``fal`` are built in; add
another with ``register_provider``. ``create_image_client`` is what the dashboard calls: it
reads ``visuals.ai_image`` from config/settings.yaml and returns the configured provider's
client, or None when its key is not set (the pipeline then skips AI images, as it always did
without OPENAI_API_KEY).
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from hyperframes_mcp.providers.image.client import AIImageClient
from hyperframes_mcp.providers.image.fal_client import FalImageClient
from hyperframes_mcp.providers.image.models import AIImageResult
from hyperframes_mcp.providers.image.settings import ImageSettings, load_image_settings


class ImageClient(Protocol):
    """What the service needs from a provider's client."""

    provider_name: str
    model: str

    async def generate(self, prompt: str, preset: str = "draft") -> AIImageResult | None:
        """One image, or None when the provider has no API key."""
        ...


class UnknownProviderError(ValueError):
    """The configured provider is not registered."""


@dataclass(frozen=True)
class ProviderSpec:
    """A registered provider. ``build(api_key, settings, max_concurrent)`` makes its client."""

    name: str
    api_key_env: str
    build: Callable[[str | None, ImageSettings, int], ImageClient]


_REGISTRY: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> None:
    """Add (or replace) a provider."""
    _REGISTRY[spec.name] = spec


def get_provider(name: str) -> ProviderSpec:
    """The provider called ``name``; raises ``UnknownProviderError`` listing the known ones."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownProviderError(
            f"Unknown image provider {name!r}; known: {', '.join(available_providers())}"
        ) from None


def available_providers() -> list[str]:
    """Registered provider names, sorted."""
    return sorted(_REGISTRY)


def create_image_client(
    settings: ImageSettings | None = None,
    env: Mapping[str, str] | None = None,
    max_concurrent: int = 3,
) -> ImageClient | None:
    """The client for the configured provider, or None if its API key is not set.

    Args:
        settings: Defaults to ``visuals.ai_image`` in config/settings.yaml.
        env: Defaults to the process environment.
        max_concurrent: Simultaneous requests the client allows.

    Raises:
        UnknownProviderError: the configured provider is not registered.
    """
    settings = settings if settings is not None else load_image_settings()
    spec = get_provider(settings.provider)
    api_key = (env if env is not None else os.environ).get(spec.api_key_env)
    if not api_key:
        return None
    return spec.build(api_key, settings, max_concurrent)


register_provider(
    ProviderSpec(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        build=lambda key, settings, limit: AIImageClient(api_key=key, max_concurrent=limit, settings=settings),
    )
)
register_provider(
    ProviderSpec(
        name="fal",
        api_key_env="FAL_KEY",
        build=lambda key, settings, limit: FalImageClient(api_key=key, settings=settings, max_concurrent=limit),
    )
)
