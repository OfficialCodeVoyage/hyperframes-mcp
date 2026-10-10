"""Per-image cost estimates by provider, model, size and quality.

The table is data: ``config/settings.yaml`` under ``visuals.ai_image.pricing`` (see
``settings.load_image_settings``), merged over ``DEFAULT_PRICING``, which holds only what the
client charged before this module existed (``gpt-image-1``). Each model's price is one of:

* ``flat``: one price per image.
* ``per_image``: ``{quality: {"WxH": usd}}``. A size missing from the table is priced at the
  higher of the two listed sizes either side of it (token-priced tables are not monotonic in
  size, so the next larger size alone can undershoot), and a size larger than every listed
  one is an error.
* ``per_megapixel``: ``first`` for the first megapixel plus ``extra`` for each further one,
  rounded up to whole megapixels (fal's FLUX.2 [pro] rule).

Amounts are estimates for budgeting: token-priced models (the gpt-image-2.x family) charge by
tokens, so the real price moves with prompt length.
"""

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

_SIZE_PATTERN = re.compile(r"^(\d+)x(\d+)$")

# fal's FLUX.2 [pro] page prices 1920x1080 (2,073,600 pixels) at two megapixels, which only
# rounds that way if a megapixel is 2**20 pixels (1.98 MP), not 10**6 (2.07 MP). UNVERIFIED
# beyond that one worked example.
PIXELS_PER_MEGAPIXEL = 1_048_576


class PricingError(ValueError):
    """The pricing table cannot price a request (unknown model, size or quality)."""


def parse_size(size: str) -> tuple[int, int]:
    """Split ``"1536x1024"`` into ``(1536, 1024)``."""
    match = _SIZE_PATTERN.match(size)
    if not match:
        raise PricingError(f"Size must look like '1024x1024', got {size!r}")
    return int(match.group(1)), int(match.group(2))


class MegapixelPrice(BaseModel):
    """``first`` for the first megapixel, ``extra`` for each further one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    first: float = Field(ge=0)
    extra: float = Field(default=0.0, ge=0)


class ModelPrice(BaseModel):
    """What one model charges per image; exactly one of the three price kinds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flat: float | None = Field(default=None, ge=0)
    per_image: dict[str, dict[str, float]] | None = None
    per_megapixel: MegapixelPrice | None = None
    note: str = ""  # where the figure comes from; documentation only

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> "ModelPrice":
        kinds = [kind for kind in ("flat", "per_image", "per_megapixel") if getattr(self, kind) is not None]
        if len(kinds) != 1:
            raise ValueError(f"a price needs exactly one of flat, per_image, per_megapixel; got {kinds or 'none'}")
        for sizes in (self.per_image or {}).values():
            for size, usd in sizes.items():
                parse_size(size)
                if usd < 0:
                    raise ValueError(f"negative price for {size}")
        return self

    def estimate(self, width: int, height: int, quality: str) -> float:
        """USD for one ``width`` x ``height`` image at ``quality``."""
        if self.flat is not None:
            return self.flat
        if self.per_megapixel is not None:
            rate = self.per_megapixel
            megapixels = max(1, math.ceil(width * height / PIXELS_PER_MEGAPIXEL))
            return rate.first + rate.extra * (megapixels - 1)
        return self._from_table(width, height, quality)

    def _from_table(self, width: int, height: int, quality: str) -> float:
        by_size = (self.per_image or {}).get(quality)
        if by_size is None:
            known = ", ".join(sorted(self.per_image or {}))
            raise PricingError(f"No price for quality {quality!r}; priced qualities: {known}")
        if f"{width}x{height}" in by_size:
            return by_size[f"{width}x{height}"]
        wanted = width * height
        priced = sorted((w * h, usd) for w, h, usd in ((*parse_size(size), usd) for size, usd in by_size.items()))
        above = [usd for area, usd in priced if area >= wanted]
        if not above:
            raise PricingError(
                f"No price for {width}x{height}: larger than every priced size ({', '.join(sorted(by_size))})"
            )
        below = [usd for area, usd in priced if area < wanted]
        return max(above[0], below[-1]) if below else above[0]


class PricingTable(RootModel[dict[str, dict[str, ModelPrice]]]):
    """``{provider: {model: ModelPrice}}``."""

    model_config = ConfigDict(frozen=True)

    def estimate(self, provider: str, model: str, size: str, quality: str) -> float:
        """USD for one image; raises ``PricingError`` when the table cannot price it."""
        models = self.root.get(provider)
        if models is None:
            raise PricingError(f"No pricing for provider {provider!r}; known: {', '.join(sorted(self.root))}")
        price = models.get(model)
        if price is None:
            raise PricingError(
                f"No pricing for {provider}/{model}. Add it under "
                "visuals.ai_image.pricing in config/settings.yaml "
                f"(priced models for {provider}: {', '.join(sorted(models))})"
            )
        width, height = parse_size(size)
        return price.estimate(width, height, quality)

    def landscape_size(self, provider: str, model: str, quality: str) -> str | None:
        """The listed landscape size nearest 16:9 for ``quality``, or None.

        Only a ``per_image`` table lists sizes; a flat or per-megapixel price accepts any size
        and verifies none, so None means "no verified landscape size", not "none exists".
        Ties on the aspect go to the smallest area, which is the cheapest.
        """
        return self._nearest_size(provider, model, quality, landscape=True)

    def portrait_size(self, provider: str, model: str, quality: str) -> str | None:
        """The listed portrait size nearest 9:16 for ``quality``, or None (as ``landscape_size``)."""
        return self._nearest_size(provider, model, quality, landscape=False)

    def _nearest_size(self, provider: str, model: str, quality: str, *, landscape: bool) -> str | None:
        price = self.root.get(provider, {}).get(model)
        sizes = ((price.per_image or {}).get(quality) or {}) if price else {}
        wanted = 16 / 9 if landscape else 9 / 16
        candidates = [
            (abs(w / h - wanted), w * h, size)
            for size in sizes
            for w, h in [parse_size(size)]
            if (w > h) == landscape and w != h
        ]
        return min(candidates)[2] if candidates else None

    def merged_with(self, other: "PricingTable") -> "PricingTable":
        """This table with ``other``'s models added; ``other`` wins where both price a model."""
        merged: dict[str, Any] = {p: dict(models) for p, models in self.root.items()}
        for provider, models in other.root.items():
            merged.setdefault(provider, {}).update(models)
        return PricingTable(merged)


# gpt-image-1 per-image prices, read 2026-09-30 on
# https://developers.openai.com/api/docs/models/gpt-image-1
DEFAULT_PRICING = PricingTable.model_validate(
    {
        "openai": {
            "gpt-image-1": {
                "per_image": {
                    "low": {"1024x1024": 0.011, "1024x1536": 0.016, "1536x1024": 0.016},
                    "medium": {
                        "1024x1024": 0.042,
                        "1024x1536": 0.063,
                        "1536x1024": 0.063,
                    },
                    "high": {"1024x1024": 0.167, "1024x1536": 0.25, "1536x1024": 0.25},
                },
            }
        }
    }
)
