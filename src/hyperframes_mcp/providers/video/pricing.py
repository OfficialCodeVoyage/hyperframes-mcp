"""The image-to-video models the bake-off compares, with prices and request shapes.

Every figure was read on 2026-10-01 from fal.ai's own pages by WebFetch (a small model
summarises each page, so a number is only as good as that summary; the first real run's invoice
is the check). ``usd_per_second`` is the price AT THE PINNED INPUTS in ``fixed_input``: several
endpoints default to an expensive setting (audio on, 1080p or 2K) and ``build_input`` always
sends the cheap one. Where fal's pages disagree, the higher figure is used so an estimate
cannot undershoot. A model that is not in this table cannot be priced and is refused.

Sources (page; fact):

* https://fal.ai/models/fal-ai/kling-video/v2.5-turbo/pro/image-to-video : "For 5s video your
  request will cost $0.35. For every additional second you will be charged $0.07." Pricing
  page https://fal.ai/pricing agrees ($0.07 per second). API page (.../api): ``duration`` enum
  "5", "10"; ``image_url``, ``prompt``, ``negative_prompt``, ``cfg_scale``, ``tail_image_url``.
  Output resolution is not stated. Label "Commercial use".
* https://fal.ai/models/fal-ai/kling-video/v3/pro/image-to-video : "$0.112 (audio off) or
  $0.168 (audio on), if voice control is used ... $0.196" per second, but https://fal.ai/pricing
  lists $0.14 per second for the same endpoint. The higher $0.14 is used. ``generate_audio``
  defaults to true, so it is pinned off. ``duration`` 3 to 15; the still goes in
  ``start_image_url``. Label "Commercial use", tier "Partner".
* https://fal.ai/models/fal-ai/veo3.1/lite/image-to-video : "$0.05 for 720p with audio, $0.03
  for 720p without audio, $0.08 for 1080p with audio or $0.05 for 1080p without audio" per
  second. API page: ``duration`` "4s", "6s", "8s" (default 8s), ``resolution`` 720p|1080p,
  ``aspect_ratio`` auto|16:9|9:16, ``generate_audio`` default true, images up to 8 MB. There is
  no 5 s clip. Pinned: 720p, no audio, 16:9. Label "Commercial use", tier "Partner".
* https://fal.ai/models/alibaba/wan-3.0/image-to-video : "$0.05 480p, $0.10 720p, or $0.20
  1080p" per second (the page does not say whether audio changes the price; ``audio`` defaults
  to true and is pinned off). https://fal.ai/wan-3 : "2 to 30 seconds in a single generation".
  API page: ``start_image_url``, ``duration`` integer (default 5), ``resolution`` default 1080p,
  ``aspect_ratio`` adaptive|16:9|..., ``enable_safety_checker`` default true. Label "Commercial
  use", tier "Partner".
* https://fal.ai/models/minimax/h3/image-to-video : "$0.05 per second at 480p, $0.06 per second
  at 768p, $0.13 per second at 2K and $0.16 per second at 4K". API page: ``resolution`` 480P|
  768P|2K|4K with default 2K (pinned to 768P), ``duration`` integer default 5 (other values
  UNVERIFIED, so only 5 is allowed here), ``image_url`` optional. Label "Commercial use".

Licence: all five pages carry fal's "Commercial use" label. The partner terms behind it (Kling,
Google, MiniMax, Alibaba) were NOT read: UNVERIFIED. Output resolution is UNVERIFIED for the
two Kling endpoints. Left out after checking (see docs/i2v-bakeoff.md): Seedance
2.5 (about $0.47 per second at 720p by fal's token formula), Luma Ray 2 ($1.00 per 5 s at 720p),
Luma Ray 3 (no price on its page), H3 Max, Veo 3.1 Fast and Standard.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from hyperframes_mcp.providers.video.errors import ClipLengthError, UnknownModelError

VERIFIED_ON = "2026-10-01"
DurationStyle = Literal["str", "int", "str_s"]


@dataclass(frozen=True)
class I2VModel:
    """One fal.ai I2V endpoint: its price at the pinned inputs and how to call it."""

    key: str
    endpoint: str
    family: str
    usd_per_second: float
    durations: tuple[int, ...]
    output: str
    price_sources: tuple[str, ...]
    image_field: str = "image_url"
    duration_style: DurationStyle = "str"
    fixed_input: Mapping[str, Any] = field(default_factory=dict)
    verified_on: str = VERIFIED_ON
    notes: str = ""

    def build_input(self, image_url: str, prompt: str, seconds: int) -> dict[str, Any]:
        """The JSON body for fal's queue: the still, the motion prompt, the length, pinned options."""
        duration: str | int = {
            "str": str(seconds),
            "str_s": f"{seconds}s",
            "int": seconds,
        }[self.duration_style]
        return {
            self.image_field: image_url,
            "prompt": prompt,
            "duration": duration,
            **self.fixed_input,
        }


_MODELS = (
    I2VModel(
        key="kling-2.5-turbo-pro",
        endpoint="fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        family="Kling",
        usd_per_second=0.07,
        durations=(5, 10),
        output="resolution not stated",
        price_sources=(
            "https://fal.ai/models/fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
            "https://fal.ai/pricing",
        ),
    ),
    I2VModel(
        key="kling-3-pro",
        endpoint="fal-ai/kling-video/v3/pro/image-to-video",
        family="Kling",
        usd_per_second=0.14,
        durations=tuple(range(3, 16)),
        output="resolution not stated",
        price_sources=(
            "https://fal.ai/models/fal-ai/kling-video/v3/pro/image-to-video",
            "https://fal.ai/pricing",
        ),
        image_field="start_image_url",
        fixed_input={"generate_audio": False},
        notes="model page says $0.112 with audio off; pricing page says $0.14; higher used",
    ),
    I2VModel(
        key="veo-3.1-lite",
        endpoint="fal-ai/veo3.1/lite/image-to-video",
        family="Veo",
        usd_per_second=0.03,
        durations=(4, 6, 8),
        output="720p",
        price_sources=("https://fal.ai/models/fal-ai/veo3.1/lite/image-to-video",),
        duration_style="str_s",
        fixed_input={
            "resolution": "720p",
            "generate_audio": False,
            "aspect_ratio": "16:9",
        },
        notes="no 5 s clip: 4, 6 or 8 s",
    ),
    I2VModel(
        key="wan-3.0",
        endpoint="alibaba/wan-3.0/image-to-video",
        family="Wan",
        usd_per_second=0.10,
        durations=tuple(range(2, 31)),
        output="720p",
        price_sources=(
            "https://fal.ai/models/alibaba/wan-3.0/image-to-video",
            "https://fal.ai/wan-3",
        ),
        image_field="start_image_url",
        duration_style="int",
        fixed_input={"resolution": "720p", "audio": False, "aspect_ratio": "16:9"},
        notes="whether audio changes the price is UNVERIFIED",
    ),
    I2VModel(
        key="minimax-h3",
        endpoint="minimax/h3/image-to-video",
        family="MiniMax Hailuo",
        usd_per_second=0.06,
        durations=(5,),
        output="768p",
        price_sources=("https://fal.ai/models/minimax/h3/image-to-video",),
        duration_style="int",
        fixed_input={"resolution": "768P"},
        notes="durations other than 5 are UNVERIFIED on fal's page",
    ),
)

I2V_MODELS: Mapping[str, I2VModel] = MappingProxyType({m.key: m for m in _MODELS})


def get_model(name: str) -> I2VModel:
    """The model called ``name`` (its key or its fal endpoint id).

    Raises:
        UnknownModelError: it is not in the table. An unpriced model is never called.
    """
    for model in I2V_MODELS.values():
        if name in (model.key, model.endpoint):
            return model
    raise UnknownModelError(f"Unknown I2V model {name!r}; priced models: {', '.join(I2V_MODELS)}")


def _check_seconds(model: I2VModel, seconds: int) -> None:
    if seconds not in model.durations:
        allowed = ", ".join(str(d) for d in model.durations)
        raise ClipLengthError(f"{model.key} cannot make a {seconds} s clip; allowed seconds: {allowed}")


def estimate_clip_cost(model: str, seconds: int) -> float:
    """USD for one ``seconds`` clip on ``model`` (a list-price estimate, not an invoice).

    Raises:
        UnknownModelError: the model is not priced.
        ClipLengthError: ``seconds`` is not a length the model supports.
    """
    chosen = get_model(model)
    _check_seconds(chosen, seconds)
    return round(chosen.usd_per_second * seconds, 6)


def nearest_duration(model: str, target_seconds: int) -> int:
    """The shortest length ``model`` supports that is at least ``target_seconds``.

    Raises:
        ClipLengthError: the model cannot make a clip that long.
    """
    chosen = get_model(model)
    longer = [d for d in chosen.durations if d >= target_seconds]
    if not longer:
        raise ClipLengthError(
            f"{chosen.key} cannot make a clip of {target_seconds} s or more; longest is {max(chosen.durations)} s"
        )
    return min(longer)
