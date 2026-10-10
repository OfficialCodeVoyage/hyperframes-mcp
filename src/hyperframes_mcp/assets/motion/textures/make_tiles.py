"""Build the three ``finish`` overlay tiles from two ambientCG CC0 maps (see TEXTURES.md).

    python assets/motion/textures/make_tiles.py SCRATCHES_OPACITY.jpg DUST_OPACITY.jpg OUT_DIR

The inputs are the ``_Opacity.jpg`` maps of ``Scratches003`` and ``SurfaceImperfections015`` (2K JPG). Each tile is a
1920x1080 RGBA PNG: sparse light specks and thin light scratches in the paper colour, a few dark specks in the ink colour,
everything else fully transparent. The three tiles are different windows of the two maps, mirrored differently, so the
dust looks new when the block steps from one to the next.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageFilter

_COLOURS = json.loads((Path(__file__).parents[1] / "tokens.json").read_text())["colors"]
PAPER = ImageColor.getrgb(_COLOURS["paper"])
INK = ImageColor.getrgb(_COLOURS["ink"])
WIDTH, HEIGHT = 1920, 1080
# (x offset, y offset, mirrored left to right, mirrored top to bottom) of the window of each tile
VIEWS = [(0, 0, False, False), (128, 968, True, False), (60, 520, False, True)]
SPECK = 5  # px: a speck is a bright or dark spot smaller than this
# top-hat levels where light and dark specks start, and how far above them a speck reaches full strength
LIGHT_FROM = 0.52
DARK_FROM = 0.42
SPAN = 0.25
SCRATCH_FROM, SCRATCH_SPAN, SCRATCH_ALPHA = 0.20, 0.40, 0.8
DARK_ALPHA = 0.8


def load(path: str | Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L")).astype(np.float32) / 255.0


def tophat(a: np.ndarray, size: int, white: bool) -> np.ndarray:
    """The bright (``white``) or dark spots of ``a`` that are smaller than ``size`` pixels."""
    image = Image.fromarray((a * 255).astype(np.uint8))
    if white:
        opened = image.filter(ImageFilter.MinFilter(size)).filter(
            ImageFilter.MaxFilter(size)
        )
        return np.clip(a - np.asarray(opened).astype(np.float32) / 255.0, 0, 1)
    closed = image.filter(ImageFilter.MaxFilter(size)).filter(
        ImageFilter.MinFilter(size)
    )
    return np.clip(np.asarray(closed).astype(np.float32) / 255.0 - a, 0, 1)


def tile(scratch: np.ndarray, dust: np.ndarray, k: int) -> Image.Image:
    """Tile ``k`` (0 to 2): scratches and dust windows of the two maps combined into one RGBA image."""
    x, y, flip_x, flip_y = VIEWS[k]
    s = scratch[y : y + HEIGHT, x : x + WIDTH]
    dy, dx = (y + 300) % 968, (x + 90) % 128
    d = dust[dy : dy + HEIGHT, dx : dx + WIDTH]
    if flip_x:
        s, d = s[:, ::-1], d[:, ::-1]
    if flip_y:
        s, d = s[::-1], d[::-1]
    light_dust = np.clip((tophat(d, SPECK, True) - LIGHT_FROM) / SPAN, 0, 1)
    dark_dust = np.clip((tophat(d, SPECK, False) - DARK_FROM) / SPAN, 0, 1)
    scratches = np.clip((s - SCRATCH_FROM) / SCRATCH_SPAN, 0, 1) * SCRATCH_ALPHA
    light = np.maximum(light_dust, scratches)
    dark = dark_dust > light
    rgba = np.zeros((HEIGHT, WIDTH, 4), np.uint8)
    rgba[..., :3] = np.where(dark[..., None], INK, PAPER)
    alpha = np.where(dark, dark_dust * DARK_ALPHA, light)
    rgba[..., 3] = (alpha * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(rgba)


def main(scratches: str, dust: str, out: str) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch_map, dust_map = load(scratches), load(dust)
    for k, name in enumerate("abc"):
        tile(scratch_map, dust_map, k).save(
            out_dir / f"finish-dust-{name}.png", optimize=True
        )


if __name__ == "__main__":
    main(*sys.argv[1:4])
