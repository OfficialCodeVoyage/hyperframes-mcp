"""The drawing of ``bar-grid``: which cell is lit when, the outline of a segment, and the draw as a function of the time.

The block's own functions run in Node; the ticker is a real GSAP timeline whose modifier calls the block's ``drawFrame``
with a recording stand-in for the canvas context, seeked forward, backward, in a shuffled order and the way the
HyperFrames runtime seeks (a quiet seek just after, then the real one).
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from tests.motion.bar_grid_helpers import (
    BLOCK,
    COLD_OPEN,
    DRAW_PRELUDE,
    FOR_SCALE,
    SMALL,
    js,
)
from tests.motion.node_logic import needs_node, run

# -- which cell is lit when ------------------------------------------------------------------------


@needs_node
def test_a_cell_starts_in_order_along_the_sweep_and_every_cell_is_settled_when_the_segment_is_full():
    program = """
    const s = { first: 0, count: 100, at: 1, seconds: 2, trail: 0.5, sweep: 1.5 };
    const at = (t) => startedCells(s, t);
    const frames = [];
    for (let f = 0; f <= 96; f += 1) frames.push(at(f / 24));
    emit({
      before: at(0.99), first: at(1), mid: at(1.75), end: at(3), after: at(30),
      mono: frames.every((n, i) => i === 0 || n >= frames[i - 1]),
      levels: [0, 1, 49, 50, 99].map((k) => [levelAt(s, k, 3), levelAt(s, k, 1)]),
      trailing: [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6].map((t) => levelAt(s, 0, t)),
    });
    """
    out = run(BLOCK, program)

    assert out["before"] == 0 and out["first"] == 1
    assert out["mid"] == 51  # cells 0 to 50 have started: cell 50 starts at 1.75
    assert out["end"] == out["after"] == 100
    assert out["mono"]
    # every cell is settled at at + seconds; at the first instant only cell 0 is lit
    assert [pair[0] for pair in out["levels"]] == [8] * 5
    assert [pair[1] for pair in out["levels"]] == [1, 0, 0, 0, 0]
    # the trail of one cell: it comes up level by level and has settled when the trail is over
    assert out["trailing"][0] == 1 and out["trailing"][-1] == 8
    assert out["trailing"] == sorted(out["trailing"])


@needs_node
def test_the_levels_of_a_cell_climb_through_the_flash_to_the_settled_tone():
    program = """
    const s = { first: 0, count: 1, at: 0, seconds: 2, trail: 0.5, sweep: 1.5 };
    const levels = [];
    for (let f = 0; f <= 12; f += 1) levels.push(levelAt(s, 0, f / 24));
    emit({ levels: levels, look: LOOK, LEVELS: LEVELS });
    """
    out = run(BLOCK, program)

    assert out["levels"][0] == 1 and out["levels"][-1] == 8
    assert out["levels"] == sorted(out["levels"])
    assert set(out["levels"]) == set(range(1, 9))  # none is skipped at 24 frames a second
    look = out["look"]
    assert len(look) == out["LEVELS"] + 1 and look[0] is None
    assert look[8] == [1, 0], "the settled cell is the plain tone"
    assert look[4] == [1, 1], "the flash is the brightest tint, halfway up the trail"
    assert max(range(1, 9), key=lambda i: look[i][1]) == 4
    assert [look[i][0] for i in range(1, 9)] == sorted(look[i][0] for i in range(1, 9))


@needs_node
def test_the_count_of_a_segment_is_the_number_of_cells_started_and_lands_exactly():
    program = f"""
    const cfg = readConfig({js(FOR_SCALE)});
    const [a, b] = cfg.segments;
    const rows = [];
    for (let f = 0; f <= 12 * 24; f += 1) rows.push([startedCells(a, f / 24), startedCells(b, f / 24)]);
    emit(rows);
    """
    rows = run(BLOCK, program)

    assert rows[0] == [0, 0]
    assert rows[-1] == [3600, 320]
    for before, after in pairwise(rows):
        assert after[0] >= before[0] and after[1] >= before[1]
    # the gold count does not start before its segment does, and the ember one is done by then
    first_gold = next(i for i, r in enumerate(rows) if r[1] > 0)
    assert first_gold / 24 == pytest.approx(4.6, abs=1 / 24)
    assert rows[first_gold][0] == 3600
    assert next(i for i, r in enumerate(rows) if r[0] == 3600) / 24 <= 0.6 + 3.0 + 1 / 24


# -- the outline of a segment ----------------------------------------------------------------------


@needs_node
def test_the_outline_of_a_segment_encloses_exactly_its_cells():
    program = """
    const bad = [];
    const area = (pts) => {
      let sum = 0;
      for (let i = 0; i < pts.length; i += 1) {
        const [x1, y1] = pts[i];
        const [x2, y2] = pts[(i + 1) % pts.length];
        sum += x1 * y2 - x2 * y1;
      }
      return sum / 2;
    };
    for (const cols of [1, 2, 3, 5, 10, 13]) {
      for (let first = 0; first < cols * 3; first += 1) {
        for (let count = 1; count <= cols * 4; count += 1) {
          const shapes = outlineShapes(first, count, cols);
          const tag = cols + "/" + first + "/" + count;
          const total = shapes.reduce((n, s) => n + area(s), 0);
          // clockwise on the screen gives a positive area, and the areas are the cells
          if (shapes.some((s) => area(s) <= 0)) bad.push(tag + " orientation");
          if (total !== count) bad.push(tag + " area " + total);
          for (const s of shapes) {
            // axis-aligned edges only, no repeated or collinear points
            for (let i = 0; i < s.length; i += 1) {
              const a = s[i], b = s[(i + 1) % s.length], c = s[(i + 2) % s.length];
              if (a[0] !== b[0] && a[1] !== b[1]) bad.push(tag + " slanted");
              if ((b[0] - a[0]) * (c[1] - b[1]) === (b[1] - a[1]) * (c[0] - b[0])) bad.push(tag + " collinear");
            }
          }
        }
      }
    }
    emit({
      bad: bad,
      oneRow: outlineShapes(4, 6, 20), partial: outlineShapes(5, 31, 10), full: outlineShapes(0, 30, 10),
      apart: outlineShapes(8, 5, 10), whole: outlineShapes(0, 8198, 123),
    });
    """
    out = run(BLOCK, program)

    assert out["bad"] == []
    assert out["oneRow"] == [[[4, 0], [10, 0], [10, 1], [4, 1]]]
    # a first row from column 5, two whole rows and a last row to column 6: eight corners
    assert len(out["partial"]) == 1 and len(out["partial"][0]) == 8
    assert out["full"] == [[[0, 0], [10, 0], [10, 3], [0, 3]]]
    # the last row ends before the first begins: two rectangles
    assert len(out["apart"]) == 2 and all(len(s) == 4 for s in out["apart"])
    # the whole grid of the cold open: its rows and a short last row, six corners
    assert len(out["whole"]) == 1 and len(out["whole"][0]) == 6


@needs_node
def test_inflating_an_outline_moves_every_edge_out_by_the_same_distance():
    program = """
    const m = { px: 14, py: 9 };  // the margin of the canvas (20 px) is the block's own constant
    const rect = [[2, 1], [6, 1], [6, 3], [2, 3]];
    const l = [[3, 0], [10, 0], [10, 2], [4, 2], [4, 3], [0, 3], [0, 1], [3, 1]];
    const bounds = (pts) => [Math.min(...pts.map((p) => p[0])), Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1]))];
    emit({ at0: inflated(rect, m, 0), grown: bounds(inflated(rect, m, 5)), flat: bounds(inflated(rect, m, 0)), l0: inflated(l, m, 0), l3: inflated(l, m, 3) });
    """
    out = run(BLOCK, program)

    assert out["at0"] == [[48, 29], [104, 29], [104, 47], [48, 47]]
    grown, flat = out["grown"], out["flat"]
    assert grown == [flat[0] - 5, flat[1] - 5, flat[2] + 5, flat[3] + 5]
    # at distance 0 the corners are the cell corners; at 3 each corner of the L moves along its own diagonal, the
    # reflex ones too (the two inner corners of the notch move into it)
    assert out["l0"][0] == [20 + 3 * 14, 20]
    moves = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(out["l0"], out["l3"], strict=True)]
    assert moves == [
        (-3, -3),
        (3, -3),
        (3, 3),
        (3, 3),
        (3, 3),
        (-3, 3),
        (-3, -3),
        (-3, -3),
    ]


# -- the draw, as a function of the time alone -----------------------------------------------------


@needs_node
def test_every_cell_has_its_own_place_and_a_lit_glyph_lands_exactly_on_its_empty_one():
    program = (
        DRAW_PRELUDE
        + f"""
    const b = build({js(COLD_OPEN)});
    const m = b.model;
    const empty = recorder();
    drawEmptyCells(empty, m, "empty", b.cfg.total);
    const lit = recorder();
    drawFrame(lit, m, b.sprites, 6);
    const place = (op) => op[2] + "," + op[3];
    emit({{
      n: empty.ops.length, empty: empty.ops.map(place), lit: lit.ops.filter((o) => o[0] === "image").map(place),
      first: empty.ops[0], second: empty.ops[1], row: empty.ops[m.cols], last: empty.ops[empty.ops.length - 1],
      canvas: [m.canvasW, m.canvasH], glyph: [b.layout.glyph.w, b.layout.glyph.h],
    }});
    """
    )
    out = run(BLOCK, program)

    assert out["n"] == 8198 and len(set(out["empty"])) == 8198
    # the margin (20 px) and the glyph's offset in its cell (1 px), then one pitch (14 px, 9 px) to the next cell
    assert out["first"] == ["image", "empty", 21, 21, 1]
    assert out["second"][2:4] == [35, 21] and out["row"][2:4] == [21, 30]
    assert out["last"][2:4] == [
        20 + 79 * 14 + 1,
        20 + 66 * 9 + 1,
    ]  # cell 8197: column 79 of row 66
    # every glyph is inside the canvas
    width, height = out["canvas"]
    gw, gh = out["glyph"]
    assert all(int(x) + gw <= width and int(y) + gh <= height for x, y in (p.split(",") for p in out["empty"]))
    # at 6 s every cell of the cold open is lit, each exactly where its empty glyph was drawn
    assert out["lit"] == out["empty"]


@needs_node
def test_the_cells_drawn_are_the_cells_started_and_no_mark_comes_before_a_segment_is_full():
    program = (
        DRAW_PRELUDE
        + f"""
    const b = build({js(SMALL)});
    const rec = recorder();
    const out = [];
    for (const t of [0, 0.29, 0.3, 1.0, 2.3, 2.6, 3.0, 3.6, 5, 8]) {{
      drawFrame(rec, b.model, b.sprites, t);
      const images = rec.ops.filter((op) => op[0] === "image").length;
      const expected = b.cfg.segments.reduce((n, s) => n + startedCells(s, t), 0);
      out.push([t, images, expected, rec.ops.filter((op) => op[0] === "stroke").length, rec.ops.filter((op) => op[0] === "fill").length]);
    }}
    emit(out);
    """
    )
    out = run(BLOCK, program)

    for t, images, expected, _, _ in out:
        assert images == expected, t
    by_time = {t: (images, strokes, fills) for t, images, _, strokes, fills in out}
    assert by_time[0][0] == 0 and by_time[0.29][0] == 0
    assert by_time[0.3][0] == 1  # the first cell lights at its segment's own time
    assert by_time[5][0] == 790 and by_time[8][0] == 790
    # nothing marks a segment before it is full: no wash, no frame, no ring
    for t in (0, 0.29, 0.3, 1.0):
        assert by_time[t][1:] == (0, 0), t
    # strokes, fills: the ember one is full at 2.3 s, so at 2.6 s it has its frame and a ring and a wash; at 3.0 s the
    # ring has gone; the gold one is full at 3.6 s, with its own ring; at 5 s and later two frames and two washes stay
    assert by_time[2.3][1:] == (
        2,
        1,
    )  # at the instant it is full the ring is at its strongest and the frame not yet in
    assert by_time[2.6][1:] == (2, 1)
    assert by_time[3.0][1:] == (1, 1)
    assert by_time[3.6][1:] == (3, 2)
    assert by_time[5][1:] == (2, 2) and by_time[8][1:] == (2, 2)


@needs_node
def test_a_full_segment_gets_a_wash_a_frame_and_one_spreading_ring_that_then_goes():
    program = (
        DRAW_PRELUDE
        + f"""
    const b = build({js(SMALL)});
    const rec = recorder();
    const ember = b.cfg.segments[0];
    const full = ember.at + ember.seconds;
    const probe = (u) => {{
      drawFrame(rec, b.model, b.sprites, full + u);
      const ops = rec.ops.filter((op) => op[0] !== "image" && op[1] === "E");
      return {{
        fills: ops.filter((o) => o[0] === "fill").map((o) => o[2]),
        frames: ops.filter((o) => o[0] === "stroke" && o[2] === OUTLINE_PX).map((o) => o[3]),
        rings: ops.filter((o) => o[0] === "stroke" && o[2] === OUTLINE_PX + 1).map((o) => o[3]),
        paths: ops.filter((o) => o[0] === "stroke").map((o) => o[4]),
      }};
    }};
    emit({{ start: probe(0), sixth: probe(ENTER / 6), mid: probe(ENTER / 2), end: probe(ENTER), later: probe(ENTER * 3) }});
    """
    )
    out = run(BLOCK, program)
    near = pytest.approx

    # at the instant it is full: the wash and the frame are faded to nothing, the ring is at its strongest
    assert out["start"]["fills"] == [near(0)] and out["start"]["frames"] == [near(0)]
    assert out["start"]["rings"] == [near(0.9)]
    # a sixth of the pulse in: the wash and the frame are half way to their strength (they come in over a third of it)
    assert out["sixth"]["fills"] == [near(0.16 * 0.5, abs=1e-9)]
    assert out["sixth"]["frames"] == [near(0.9 * 0.5, abs=1e-9)]
    # from a third on they stay at full strength; the ring fades while it spreads and is gone when the pulse is over
    assert out["mid"]["fills"] == [near(0.16)] and out["later"]["fills"] == [near(0.16)]
    assert out["mid"]["frames"] == [near(0.9)] and out["later"]["frames"] == [near(0.9)]
    assert out["mid"]["rings"] == [near(0.9 * 0.25, abs=1e-9)]
    assert out["end"]["rings"] == [] and out["later"]["rings"] == []
    # the ring is the frame spread outward: its path is the frame's path, grown
    frame, ring = out["mid"]["paths"]
    assert len(ring) == len(frame)
    assert ring[0][0] < frame[0][0] and ring[0][1] < frame[0][1]  # the top left corner moves up and left
    assert ring[1][0] > frame[1][0] and ring[1][1] < frame[1][1]  # the top right one up and right


@needs_node
def test_the_small_segment_of_the_for_scale_shot_is_tinted_and_framed_in_gold_when_it_fills():
    program = (
        DRAW_PRELUDE
        + f"""
    const b = build({js(FOR_SCALE)});
    const rec = recorder();
    const out = {{}};
    for (const t of [4.0, 5.0, 5.9, 6.2, 9]) {{
      drawFrame(rec, b.model, b.sprites, t);
      out[t] = rec.ops.filter((o) => o[0] !== "image" && o[1] === "G").map((o) => o[0]).sort();
    }}
    emit({{ out: out, shapes: b.model.shapes[1] }});
    """
    )
    out = run(BLOCK, program)
    marks, shapes = out["out"], out["shapes"]

    assert marks["4"] == [] and marks["5"] == []
    assert marks["5.9"] == ["fill", "stroke", "stroke"]  # wash, frame and ring
    assert marks["6.2"] == ["fill", "stroke", "stroke"]
    assert marks["9"] == ["fill", "stroke"]  # the ring has gone, the frame stays
    # 320 of 8198 cells from cell 3600 of 123 columns: about two and a half rows of gold, framed in one polygon
    assert len(shapes) == 1 and len(shapes[0]) == 8


@needs_node
def test_the_frame_is_the_same_whatever_order_the_times_are_seeked_in():
    """A real GSAP timeline whose ticker draws through a modifier: forward, backward, shuffled and the way the
    HyperFrames runtime seeks (a quiet seek just after, then the real one) draw the same frame for the same time."""
    program = (
        DRAW_PRELUDE
        + f"""
    const b = build({js(SMALL)});
    const rec = recorder();
    const duration = 8;
    const tl = gsap.timeline({{ paused: true }});
    const clock = addTicker(tl, duration, (value) => drawFrame(rec, b.model, b.sprites, value));
    const times = [];
    for (let f = 0; f <= duration * 24; f += 2) times.push(f / 24);
    function seek(t, quiet) {{
      if (quiet) tl.totalTime(t + 0.001, true);
      tl.totalTime(t, false);
      const direct = recorder();
      drawFrame(direct, b.model, b.sprites, clock.t);
      return [signature(rec.ops), signature(direct.ops)];
    }}
    emit({{
      times: times,
      forward: times.map((t) => seek(t, false)),
      backward: times.slice().reverse().map((t) => seek(t, false)).reverse(),
      shuffled: times.map((_, i) => times[(i * 37) % times.length]).map((t) => [t, seek(t, false)]),
      quiet: times.map((t) => seek(t, true)),
      jump: [seek(8, false), seek(0, false), seek(5.25, false)],
    }});
    """
    )
    out = run(BLOCK, program)
    forward = out["forward"]
    by_time = dict(zip(out["times"], forward, strict=True))

    assert len(forward) == len(out["times"])
    # the timeline's frame is the pure draw at the value the proxy holds
    assert all(timeline == direct for timeline, direct in forward)
    assert out["backward"] == forward
    assert out["quiet"] == forward
    assert all(sig == by_time[t] for t, sig in out["shuffled"])
    # a jump to the end, back to the start and to the middle draws what a walk to those times does
    end, start, mid = out["jump"]
    assert end == by_time[8] and start == by_time[0] and mid[0] == mid[1]
    # the frames differ while things move, and the last two (everything full, the ring gone) are the same
    assert len({timeline for timeline, _ in forward}) > 20
    assert forward[-1] == forward[-2]


@needs_node
def test_from_settledat_on_the_picture_never_changes_so_the_page_may_draw_it_once_and_copy_it():
    program = (
        DRAW_PRELUDE
        + f"""
    const sig = (V, t) => {{
      const b = build(V);
      const rec = recorder();
      drawFrame(rec, b.model, b.sprites, t);
      return signature(rec.ops);
    }};
    const out = {{}};
    for (const [name, V] of [["small", {js(SMALL)}], ["cold", {js(COLD_OPEN)}], ["scale", {js(FOR_SCALE)}]]) {{
      const at = build(V).model.settledAt;
      out[name] = {{ settledAt: at, before: sig(V, at - 0.1), at: sig(V, at), later: [at + 0.001, at + 1, at + 100].map((t) => sig(V, t)) }};
    }}
    emit(out);
    """
    )
    out = run(BLOCK, program)

    # the last segment is full, then the entrance time of its ring
    assert out["small"]["settledAt"] == pytest.approx(2.6 + 1.0 + 0.7)
    assert out["cold"]["settledAt"] == pytest.approx(0.4 + 3.0 + 0.7)
    assert out["scale"]["settledAt"] == pytest.approx(4.6 + 1.2 + 0.7)
    for name, got in out.items():
        assert got["later"] == [got["at"]] * 3, name
        assert got["before"] != got["at"], name  # a moment earlier the ring is still spreading
