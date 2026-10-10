"""The AI-image backend as configured in ``config/settings.yaml`` (``visuals.ai_image``).

``provider`` and ``model`` choose the backend, ``presets`` map a preset name ("draft",
"production") to a quality and a size, and ``pricing`` prices every model. Defaults are what
the pipeline did before any of this was configurable: OpenAI ``gpt-image-1``, draft = low
1024x1024, production = medium 1024x1024.

Settings validate that every preset can be priced, so a model missing from the pricing table
fails when the settings load rather than after images have been paid for.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hyperframes_mcp.providers.image.pricing import (
    DEFAULT_PRICING,
    PricingTable,
    parse_size,
)
from hyperframes_mcp.util.paths import config_path

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-image-1"
FALLBACK_PRESET = "draft"
# "draft_landscape" is the preset "draft" at the landscape size the price table verifies.
LANDSCAPE_SUFFIX = "_landscape"
# "draft_portrait" is the same at the portrait size nearest 9:16, for a 9:16 Short.
PORTRAIT_SUFFIX = "_portrait"
SETTINGS_PATH = config_path("settings.yaml")


class ImagePreset(BaseModel):
    """A quality level and an output size such as ``"1536x1024"``."""

    model_config = ConfigDict(frozen=True)

    quality: str
    size: str

    @field_validator("size")
    @classmethod
    def _size_is_width_x_height(cls, size: str) -> str:
        parse_size(size)
        return size

    @property
    def width(self) -> int:
        return parse_size(self.size)[0]

    @property
    def height(self) -> int:
        return parse_size(self.size)[1]


DEFAULT_PRESETS = {
    "draft": ImagePreset(quality="low", size="1024x1024"),
    "production": ImagePreset(quality="medium", size="1024x1024"),
}


class ImageSettings(BaseModel):
    """Provider, model, presets and pricing for AI image generation."""

    model_config = ConfigDict(frozen=True)

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    presets: dict[str, ImagePreset] = Field(default_factory=lambda: dict(DEFAULT_PRESETS))
    pricing: PricingTable = DEFAULT_PRICING

    @model_validator(mode="after")
    def _every_preset_is_priced(self) -> "ImageSettings":
        if FALLBACK_PRESET not in self.presets:
            raise ValueError(f"presets must include {FALLBACK_PRESET!r}")
        for name in self.presets:
            self.unit_cost(name)
        return self

    def preset(self, name: str) -> ImagePreset:
        """The preset called ``name``; an unknown name means ``"draft"``.

        ``"<name>_landscape"`` is that preset at a landscape size (see ``landscape_of``) and
        ``"<name>_portrait"`` at a portrait one (see ``portrait_of``).
        """
        if name.endswith(LANDSCAPE_SUFFIX):
            return self.landscape_of(self.preset(name.removesuffix(LANDSCAPE_SUFFIX)))
        if name.endswith(PORTRAIT_SUFFIX):
            return self.portrait_of(self.preset(name.removesuffix(PORTRAIT_SUFFIX)))
        return self.presets.get(name) or self.presets[FALLBACK_PRESET]

    def landscape_of(self, preset: ImagePreset) -> ImagePreset:
        """``preset`` at the landscape size nearest 16:9 that this model's price table lists.

        A preset that is landscape already, and a model whose price lists no sizes (flat or
        per-megapixel), keep the configured size: no size is guessed.
        """
        if preset.width > preset.height:
            return preset
        size = self.pricing.landscape_size(self.provider, self.model, preset.quality)
        return preset if size is None else ImagePreset(quality=preset.quality, size=size)

    def portrait_of(self, preset: ImagePreset) -> ImagePreset:
        """``preset`` at the portrait size nearest 9:16 that this model's price table lists.

        Where the provider can draw a portrait picture a 9:16 Short asks for one instead of
        cropping a landscape picture. A preset that is portrait already, and a model whose price
        lists no sizes (flat or per-megapixel), keep the configured size: no size is guessed, and
        the render crops (``vertical_mode`` in ``src.video_editor.models``).
        """
        if preset.height > preset.width:
            return preset
        size = self.pricing.portrait_size(self.provider, self.model, preset.quality)
        return preset if size is None else ImagePreset(quality=preset.quality, size=size)

    def unit_cost(self, preset: str) -> float:
        """Estimated USD for one image at ``preset`` on the configured provider and model."""
        chosen = self.preset(preset)
        return self.pricing.estimate(self.provider, self.model, chosen.size, chosen.quality)


def load_image_settings(settings_path: Path = SETTINGS_PATH) -> ImageSettings:
    """Read ``visuals.ai_image`` from ``config/settings.yaml``.

    A missing file or section gives the defaults. The file's pricing is merged over the
    built-in gpt-image-1 prices.

    Raises:
        ValueError: a setting is malformed, or a preset of the configured model cannot be
            priced (pydantic's ValidationError, carrying the ``PricingError`` message).
    """
    try:
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return ImageSettings()
    section = (settings.get("visuals") or {}).get("ai_image") or {}

    values: dict = {}
    for key in ("provider", "model", "presets"):
        if key in section:
            values[key] = section[key]
    if "pricing" in section:
        values["pricing"] = DEFAULT_PRICING.merged_with(PricingTable.model_validate(section["pricing"]))
    return ImageSettings(**values)
