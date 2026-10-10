"""Per-photo levelling for the ``photo-shot`` block: the exposure and contrast that put the median luma in a healthy band.

Ported from an earlier prototype which baked a levelled copy of every archival
still before the render: a percentile stretch, then a gamma that moved the median luma into 0.32 to 0.50. A photograph
of white bundles on snow or a pale wall otherwise reads milky, and a night interior reads muddy. Here the same idea is
a pair of numbers for the ``data-color-grading`` attribute, so the photograph itself stays untouched and the work is
done by the HyperFrames shader.

The shader (``applyPrimaryGrade``, hyperframes, unchanged since 0.8.101) multiplies the picture by ``2^exposure`` and then stretches it
around 0.5 by ``1 + contrast``, in display values with no gamma. Both together are one straight line,
``out = a * x - 0.5 * contrast`` with ``a = 2^exposure * (1 + contrast)``, so the gamma step of the prototype cannot be
reproduced exactly. What is kept is its purpose: the median lands inside the band, and the slope is the largest that
keeps the 0.6th and 99.5th percentiles of the picture between the black and white floors of the prototype (0.03 and 0.92),
so a flat picture gains contrast and a wide one is not clipped.

Where this differs from the prototype, with the reason:

- the slope is limited to 0.85 to 1.8 (the prototype had a gamma limit of 0.72 to 1.6): a flat or noisy picture is never
  blown up, and a wide one is never squashed;
- a median outside the band is moved to 0.02 inside its edge, not onto the edge (the prototype used the edge), so that
  rounding the two results cannot step back out;
- exposure is limited to -2 to 2 and contrast to -1 to 1, the ranges the shader documents.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

# The prototype's numbers.
BAND_LOW = 0.32
BAND_HIGH = 0.50
BLACK_FLOOR = 0.03
WHITE_FLOOR = 0.92
PERCENTILES = (0.6, 99.5)
MIN_SPREAD = 0.25
_STRIDE = 3  # every third pixel each way: a stable sample of a large photograph

# Where this port differs (see the module docstring).
BAND_MARGIN = 0.02
SLOPE_MIN = 0.85
SLOPE_MAX = 1.8
EXPOSURE_LIMIT = 2.0
CONTRAST_LIMIT = 1.0


def _luma(image_path: str | Path) -> np.ndarray:
    """Rec. 709 luma (0 to 1) of every third pixel of the image, on its display values like the prototype."""
    with Image.open(image_path) as image:
        # the stride comes first, on the 8-bit pixels: a 24 MP scan is then never held as float32
        sample = np.asarray(image.convert("RGB"))[::_STRIDE, ::_STRIDE]
    rgb = sample.astype(np.float32) / 255.0
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def median_luma(image_path: str | Path) -> float:
    """The median luma of the photograph, 0 (black) to 1 (white)."""
    return float(np.median(_luma(image_path)))


def grade_for(image_path: str | Path) -> dict[str, float]:
    """The ``exposure`` and ``contrast`` for ``data-color-grading`` that put the photograph's median luma in the band.

    A median already inside 0.32 to 0.50 stays where it is; only the contrast stretch (if the picture is flat) is
    applied. Pass the result as the ``grade`` variable of ``photo-shot``.
    """
    luma = _luma(image_path)
    median = float(np.median(luma))
    low, high = (float(p) for p in np.percentile(luma, PERCENTILES))
    high = max(high, low + MIN_SPREAD)

    if BAND_LOW <= median <= BAND_HIGH:
        target = median
    elif median < BAND_LOW:
        target = BAND_LOW + BAND_MARGIN
    else:
        target = BAND_HIGH - BAND_MARGIN

    # the line through (median, target) that keeps both ends of the picture between the floors
    room_below = (target - BLACK_FLOOR) / (median - low) if median > low else math.inf
    room_above = (WHITE_FLOOR - target) / (high - median) if high > median else math.inf
    slope = float(np.clip(min(room_below, room_above), SLOPE_MIN, SLOPE_MAX))

    contrast = float(np.clip(-2.0 * (target - slope * median), -CONTRAST_LIMIT, CONTRAST_LIMIT))
    # with the contrast fixed, the median alone decides the exposure: (m * 2^e - 0.5) * (1 + c) + 0.5 = target
    if median > 1e-6:
        gain = (target + 0.5 * contrast) / (1.0 + contrast) / median
    else:
        gain = slope / (1.0 + contrast)
    exposure = float(np.clip(math.log2(gain), -EXPOSURE_LIMIT, EXPOSURE_LIMIT))
    return {"exposure": round(exposure, 3), "contrast": round(contrast, 3)}
