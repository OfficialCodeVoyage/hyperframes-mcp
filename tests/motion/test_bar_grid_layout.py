"""The layout of ``bar-grid`` and the font tables it sizes text by, run in Node and checked against the font files."""

from __future__ import annotations

import re
from functools import cache

import pytest

from hyperframes_mcp.motion import tokens
from tests.motion.bar_grid_helpers import (
    BLOCK,
    COLD_OPEN,
    FOR_SCALE,
    SAFE_H,
    SAFE_W,
    SIZES,
    T,
    call_layout,
    call_measure,
    html,
    js,
    seg,
    variables,
)
from tests.motion.block_helpers import code_of
from tests.motion.node_logic import LogicError, needs_node, newsreader_advances, run

# -- the font tables -------------------------------------------------------------------------------


def table(name: str) -> list[int]:
    raw = re.search(rf"var {name} = \[(.*?)\];", code_of(html()), re.DOTALL).group(1)
    return [int(n) for n in raw.replace("\n", " ").replace(" ", "").strip(",").split(",")]


@cache
def label_advances() -> dict[str, float]:
    pytest.importorskip("fontTools")
    from fontTools.ttLib import TTFont

    font = TTFont(tokens.FONTS_DIR / "IBMPlexSansCondensed-Medium.ttf")
    upem = font["head"].unitsPerEm
    hmtx = font["hmtx"]
    return {chr(c): hmtx[n][0] / upem for c, n in font.getBestCmap().items()}


FACES = {
    # Newsreader at weight 600 and optical size 64: what the title is set in
    "DISPLAY_ADV": lambda: newsreader_advances(600, 64),
    "LABEL_ADV": label_advances,
}


@pytest.mark.parametrize("name", list(FACES))
def test_the_advance_tables_in_the_script_are_the_ones_of_the_font_files(name):
    got = table(name)
    real = FACES[name]()

    assert len(got) == 95
    for code, width in enumerate(got, start=32):
        assert abs(width - real[chr(code)] * 1000) <= 0.51, chr(code)


@needs_node
@pytest.mark.parametrize(
    ("text", "face", "px"),
    [
        ("Marburg: gold bars in the first partial count", "DISPLAY_ADV", 64),
        (
            "The Bundesbank, the Swiss laundry and the road to Lisbon, 1939-1945",
            "DISPLAY_ADV",
            64,
        ),
        ("W" * 62, "DISPLAY_ADV", 64),
        ("asked of Portugal (about 45 tonnes)", "LABEL_ADV", 48),
        ("Portugal turned over (about 4 tonnes)", "LABEL_ADV", 48),
        ("A Marburg-sized pile, for scale", "LABEL_ADV", 48),
        ("M" * 40, "LABEL_ADV", 48),
    ],
)
def test_the_estimate_of_a_text_width_is_never_narrower_than_the_font_and_close_to_it(text, face, px):
    """Kerning only tightens a line, so the real width without it is the one the estimate has to cover."""
    real = FACES[face]()
    drawn = sum(real[c] for c in text) * px

    estimated = call_measure(text, face, px)

    assert drawn <= estimated <= drawn * 1.08


@needs_node
def test_the_digits_of_the_count_figure_are_never_wider_than_the_estimate():
    text = code_of(html())
    digit = float(re.search(r"var DIGIT_EM = ([\d.]+);", text).group(1))
    comma = float(re.search(r"var COMMA_EM = ([\d.]+);", text).group(1))

    # weight 500 at the optical sizes the figure is drawn at: 96 px (a single segment, capped at 72) and 56 px
    for opsz in (72, 56):
        advance = newsreader_advances(500, opsz)
        assert all(advance[d] <= digit for d in "0123456789"), opsz
        assert advance[","] <= comma, opsz


# -- the grid --------------------------------------------------------------------------------------


@needs_node
def test_the_cold_open_grid_is_123_columns_of_14_by_9_pixel_cells():
    layout = call_layout(COLD_OPEN)

    assert layout["grid"] == {
        "px": 14,
        "py": 9,
        "cols": 123,
        "rows": 67,
        "width": 1722,
        "height": 603,
    }
    # 123 x 67 cells hold the 8198, with the last row short
    assert 123 * 67 >= 8198 > 123 * 66
    # a single segment is the whole story: its count is the big figure
    assert layout["countSize"] == T.sizes["title"]
    assert layout["titleH"] == layout["titleLine"]  # one line
    assert layout["glyph"]["w"] == 12 and layout["glyph"]["h"] == 7


@needs_node
def test_the_for_scale_shot_has_a_smaller_count_a_note_and_a_row_for_each_segment():
    layout = call_layout(FOR_SCALE)

    assert layout["countSize"] == T.sizes["body"]
    assert layout["grid"]["cols"] == 123 and layout["grid"]["rows"] == 67
    assert layout["countSize"] >= T.sizes["min_text"]


@needs_node
def test_the_swatch_follows_the_size_of_the_count():
    big = call_layout(COLD_OPEN)
    small = call_layout(FOR_SCALE)

    # Math.round of the figure's size times 0.45, and 3:2 wide (JavaScript rounds halves up)
    assert big["swatchH"] == 43 and big["swatchW"] == 65
    assert small["swatchH"] < big["swatchH"] and small["swatchW"] < big["swatchW"]


@needs_node
def test_a_note_adds_its_own_height_and_no_note_adds_none():
    with_note = call_layout(FOR_SCALE)
    without = call_layout(FOR_SCALE | {"note": ""})

    assert with_note["stackH"] - without["stackH"] == 24 + with_note["noteLine"]


@needs_node
def test_a_long_title_takes_two_lines_and_the_grid_still_fits():
    layout = call_layout(
        COLD_OPEN | {"title": "The Bundesbank, the Swiss laundry and the road to Lisbon, 1939 to 1945"}
    )

    assert layout["titleH"] == 2 * layout["titleLine"]
    assert layout["stackTop"] + layout["stackH"] <= T.safe["top"] + SAFE_H


@needs_node
def test_every_layout_stays_inside_the_safe_zone_and_holds_every_cell():
    """Sweep the totals, the number of segments, a note and a two-line title: nothing leaves the safe zone."""
    program = f"""
    const SAFE = {{ w: {SAFE_W}, h: {SAFE_H}, top: {T.safe["top"]} }};
    const titles = ["Gold bars", "x".repeat(70)];
    const bad = [];
    for (const total of [1, 2, 7, 50, 320, 1000, 4096, 8198, 12345, 20000]) {{
      for (const n of [1, 2, 3, 4, 5].filter((k) => k <= total)) {{
        for (const note of ["", "A note"]) {{
          for (const title of titles) {{
            const segments = [];
            for (let i = 0; i < n; i += 1) segments.push({{ count: 1, label: "found", tone: "gold", at: i, seconds: 1 }});
            const cfg = readConfig({{ title: title, total: total, unit: "bars", note: note, segments: JSON.stringify(segments) }});
            const lay = layoutFor(cfg, {{ sizes: {js(SIZES)}, safeW: SAFE.w, safeH: SAFE.h, safeTop: SAFE.top }});
            const g = lay.grid;
            const tag = [total, n, note, title.length].join("/");
            if (lay.stackTop < SAFE.top || lay.stackTop + lay.stackH > SAFE.top + SAFE.h) bad.push(tag + " stack " + lay.stackTop + "+" + lay.stackH);
            if (g.width > SAFE.w) bad.push(tag + " width " + g.width);
            if (g.height > 640 || g.height < 1) bad.push(tag + " height " + g.height);
            if (g.cols * g.rows < total || (g.rows - 1) * g.cols >= total) bad.push(tag + " cells " + g.cols + "x" + g.rows);
            if (lay.glyph.w < 1 || lay.glyph.h < 1 || lay.glyph.w > g.px || lay.glyph.h > g.py) bad.push(tag + " glyph");
            if (lay.glyph.ox + lay.glyph.w > g.px || lay.glyph.oy + lay.glyph.h > g.py) bad.push(tag + " glyph place");
          }}
        }}
      }}
    }}
    emit(bad);
    """

    assert run(BLOCK, program) == []


@needs_node
def test_the_pitch_is_the_largest_whole_pixel_one_that_fits_and_three_wide_for_two_tall():
    program = f"""
    const bad = [];
    for (const total of [1, 9, 100, 777, 3000, 8198, 20000]) {{
      for (const maxH of [200, 400, 640]) {{
        const g = chooseGrid(total, {SAFE_W}, maxH);
        if (g.px !== Math.round(g.py * ASPECT)) bad.push(total + " aspect");
        if (g.py < PITCH_MAX) {{
          // one more pixel of pitch would not have fitted
          const py = g.py + 1;
          const px = Math.round(py * ASPECT);
          const rows = Math.ceil(total / Math.floor({SAFE_W} / px));
          if (rows * py <= maxH) bad.push(total + "/" + maxH + " not the largest");
        }}
      }}
    }}
    emit({{ bad: bad, one: chooseGrid(1, {SAFE_W}, 640), many: chooseGrid(20000, {SAFE_W}, 640) }});
    """
    out = run(BLOCK, program)

    assert out["bad"] == []
    assert out["one"]["cols"] == 1 and out["one"]["rows"] == 1 and out["one"]["py"] == 32
    assert out["many"]["height"] <= 640 and out["many"]["py"] == 6


@needs_node
def test_a_grid_that_cannot_fit_is_an_error_that_says_so():
    with pytest.raises(LogicError, match="20000 cells do not fit a grid 100 px wide"):
        run(BLOCK, "chooseGrid(20000, 100, 100); emit(1);")
    # a safe zone too short for the title, the legend and a grid of the minimum height
    with pytest.raises(LogicError, match=r"leave \d+ px for the grid; at least 160"):
        call_layout(COLD_OPEN, safeH=380)


@needs_node
def test_a_label_wider_than_its_column_and_a_note_wider_than_a_line_are_refused():
    with pytest.raises(LogicError, match=r"segments\[0\]\.label .* px wide; the legend has"):
        call_layout(variables([seg(count=8198, label="W" * 38)]))
    with pytest.raises(LogicError, match="the note is too wide for one line"):
        call_layout(COLD_OPEN | {"note": "W" * 66})


@needs_node
def test_the_glyph_is_a_bar_that_is_narrower_at_the_top_and_whole_in_every_row():
    program = """
    const bad = [];
    for (let py = PITCH_MIN; py <= PITCH_MAX; py += 1) {
      const px = Math.round(py * ASPECT);
      const g = glyphOf(px, py);
      if (g.insets.length !== g.h) bad.push(py + " rows");
      if (g.insets[g.h - 1] !== 0) bad.push(py + " the bottom row is not the widest");
      for (let r = 1; r < g.h; r += 1) if (g.insets[r] > g.insets[r - 1]) bad.push(py + " not a trapezoid");
      for (const inset of g.insets) if (g.w - 2 * inset < 1) bad.push(py + " empty row");
      if (g.topRows > g.h) bad.push(py + " top rows");
    }
    emit({ bad: bad, ingot: glyphOf(14, 9), tiny: glyphOf(5, 3) });
    """
    out = run(BLOCK, program)

    assert out["bad"] == []
    assert out["ingot"]["insets"] == [2, 2, 1, 1, 1, 0, 0] and out["ingot"]["topRows"] == 2
    assert out["tiny"]["insets"] == [0, 0] and out["tiny"]["topRows"] == 0
