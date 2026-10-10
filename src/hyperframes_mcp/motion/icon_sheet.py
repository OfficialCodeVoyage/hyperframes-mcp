"""A numbered picture of icons, so the agent sees them before it picks one.

An icon's name says little (Maki's ``bank`` is a banknote, not a building). The sheet is a grid of the icons, each under its
number and id: resvg (``resvg-py``, no browser, no system library) draws each SVG and Pillow lays them out. The sheet is a
look at the candidates only: nothing is written to a project and nothing is paid.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hyperframes_mcp.motion.vectors import Icon

COLUMNS = 4
CELL_WIDTH = 220
CELL_HEIGHT = 224
ICON_SIZE = 120
ID_CHARS = 24  # an id is wrapped to lines of this many characters
_INK = "#111111"
_RULE = "#d3d3d3"


class IconSheetError(RuntimeError):
    """The sheet could not be drawn; the message says why and what to install."""


def icon_sheet(icons: Sequence[Icon], folder: Path) -> Path:
    """The PNG of ``icons`` in a grid, numbered from 1 in the order given, written into ``folder``.

    Raises:
        ValueError: ``icons`` is empty.
        IconSheetError: resvg is not installed, or cannot draw an icon.
    """
    if not icons:
        raise ValueError("an icon sheet needs at least one icon")
    columns = min(COLUMNS, len(icons))
    rows = -(-len(icons) // columns)
    sheet = Image.new("RGB", (columns * CELL_WIDTH, rows * CELL_HEIGHT), "white")
    draw = ImageDraw.Draw(sheet)
    number_font = ImageFont.load_default(size=22)
    id_font = ImageFont.load_default(size=14)
    for index, icon in enumerate(icons):
        left, top = (index % columns) * CELL_WIDTH, (index // columns) * CELL_HEIGHT
        draw.rectangle((left, top, left + CELL_WIDTH - 1, top + CELL_HEIGHT - 1), outline=_RULE)
        draw.text(
            (left + CELL_WIDTH / 2, top + 22),
            str(index + 1),
            fill=_INK,
            font=number_font,
            anchor="mm",
        )
        sheet.paste(_drawn(icon), (left + (CELL_WIDTH - ICON_SIZE) // 2, top + 44))
        lines = [icon.id[i : i + ID_CHARS] for i in range(0, len(icon.id), ID_CHARS)]
        for row, line in enumerate(lines[:3]):
            draw.text(
                (left + CELL_WIDTH / 2, top + 182 + 16 * row),
                line,
                fill=_INK,
                font=id_font,
                anchor="mm",
            )
    out = folder / "icons.png"
    sheet.save(out)
    return out


def _drawn(icon: Icon) -> Image.Image:
    """The icon as ``ICON_SIZE`` pixels square on white, in ink (``currentColor`` is the ink)."""
    try:
        import resvg_py
    except ImportError as error:
        raise IconSheetError(
            "the icon sheet needs the resvg-py package: ./venv/bin/pip install 'resvg-py>=0.5'"
        ) from error
    svg = icon.path.read_text(encoding="utf-8").replace("currentColor", _INK)
    try:
        png = resvg_py.svg_to_bytes(svg_string=svg, width=ICON_SIZE, height=ICON_SIZE, background="#ffffff")
    except ValueError as error:
        raise IconSheetError(f"{icon.id} could not be drawn: {error}") from error
    with Image.open(io.BytesIO(png)) as drawn:
        return drawn.convert("RGB")
