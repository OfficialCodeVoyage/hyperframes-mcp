"""The icon sheet: a numbered grid of icons drawn by resvg and laid out by Pillow, with no browser and no network."""

from __future__ import annotations

import pytest
from PIL import Image

from hyperframes_mcp.motion import icon_sheet, vectors


@pytest.fixture
def hits():
    found = vectors.search_icons("bank", 5)
    assert len(found) == 5
    return found


def test_the_sheet_is_a_png_the_size_of_the_grid(hits, tmp_path):
    out = icon_sheet.icon_sheet(hits, tmp_path)

    assert out == tmp_path / "icons.png"
    with Image.open(out) as sheet:
        # five icons: four columns, two rows
        assert sheet.size == (4 * icon_sheet.CELL_WIDTH, 2 * icon_sheet.CELL_HEIGHT)


def test_fewer_icons_than_columns_make_a_narrower_sheet(hits, tmp_path):
    out = icon_sheet.icon_sheet(hits[:2], tmp_path)

    with Image.open(out) as sheet:
        assert sheet.size == (2 * icon_sheet.CELL_WIDTH, icon_sheet.CELL_HEIGHT)


def test_each_icon_is_drawn_in_ink_in_its_own_cell_in_the_order_given(hits, tmp_path):
    with Image.open(icon_sheet.icon_sheet(hits[:2], tmp_path)) as sheet:
        cell = icon_sheet.CELL_WIDTH
        ink = []
        for column in range(2):
            icon_area = sheet.crop(
                (
                    column * cell + (cell - icon_sheet.ICON_SIZE) // 2,
                    44,
                    column * cell + (cell + icon_sheet.ICON_SIZE) // 2,
                    44 + icon_sheet.ICON_SIZE,
                )
            ).convert("L")
            ink.append(sum(1 for pixel in icon_area.getdata() if pixel < 128))

    assert all(count > 50 for count in ink), ink  # something was drawn in each cell


def test_two_different_icons_look_different(tmp_path):
    first, second = vectors.search_icons("arrow", 2)

    with Image.open(icon_sheet.icon_sheet([first], tmp_path)) as one:
        a = one.copy()
    with Image.open(icon_sheet.icon_sheet([second], tmp_path)) as two:
        b = two.copy()

    assert list(a.getdata()) != list(b.getdata())


def test_no_icons_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one icon"):
        icon_sheet.icon_sheet([], tmp_path)


def test_without_resvg_the_error_says_what_to_install(hits, tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "resvg_py", None)

    with pytest.raises(icon_sheet.IconSheetError, match=r"pip install 'resvg-py"):
        icon_sheet.icon_sheet(hits, tmp_path)


def test_an_icon_resvg_cannot_draw_is_named(hits, tmp_path, monkeypatch):
    import resvg_py

    def refuse(**_):
        raise ValueError("bad svg")

    monkeypatch.setattr(resvg_py, "svg_to_bytes", refuse)

    with pytest.raises(icon_sheet.IconSheetError, match=hits[0].id):
        icon_sheet.icon_sheet(hits, tmp_path)
