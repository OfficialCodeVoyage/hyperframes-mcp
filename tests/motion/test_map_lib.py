"""The helper library of the map blocks (``assets/motion/js/map-lib.js``), called under node.

The projections against the Python ones, the camera, label measuring against the font files, the label placement on seeded
scenes, the readers of the variables, the corner and the schedule. A test is skipped when node is not installed.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from hyperframes_mcp.motion import maps, tokens
from tests.motion.map_helpers import SAFE, T, apart, in_safe, needs_node, run_lib

# -- projections and the camera ---------------------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize("name", ["germany", "europe", "world"])
def test_the_script_projections_equal_the_python_ones(name):
    preset = maps.region(name)
    x0, y0, x1, y1 = preset.window
    grid = [[x0 + (x1 - x0) * i / 6, y0 + (y1 - y0) * j / 6] for i in range(7) for j in range(7)]
    points = [*grid, [10.0, 51.0], [13.405, 52.52], [8.68, 50.11], [0.0, 0.0]]
    got = run_lib(
        "input.points.map(p => lib.makeProjection(input.projection)(p[0], p[1]))",
        {"projection": preset.projection.as_json(), "points": points},
    )

    for (lon, lat), (x, y) in zip(points, got, strict=True):
        px, py = preset.projection.point(lon, lat)
        assert x == pytest.approx(px, abs=1e-6), (name, lon, lat)
        assert y == pytest.approx(py, abs=1e-6), (name, lon, lat)


@needs_node
def test_an_unknown_projection_is_an_error():
    assert "unknown projection" in run_lib("lib.makeProjection({type: 'mercator', k: 130})")["error"]


@needs_node
def test_the_camera_span_is_the_visible_width_in_degrees_and_the_zoom_is_even():
    payload = {
        "projection": maps.region("germany").projection.as_json(),
        "from": {"lon": 11.75, "lat": 51.7, "span": 9.6},
        "to": {"lon": 11.75, "lat": 51.7, "span": 8.1},
    }
    out = run_lib(
        "(() => { const p = lib.makeProjection(input.projection);"
        "const a = lib.cameraPose(p, input.from, 1920), b = lib.cameraPose(p, input.to, 1920);"
        "const at = u => lib.cameraTransform(a, b, u, 1920, 1080);"
        "return {a, b, start: at(0), mid: at(0.5), end: at(1)}; })()",
        payload,
    )

    per_degree = 130 * math.cos(math.radians(51))  # map units of one degree of longitude
    assert out["b"]["s"] == pytest.approx(1920 / (8.1 * per_degree))
    assert out["a"]["s"] == pytest.approx(1920 / (9.6 * per_degree))
    for key, pose in (
        ("start", out["a"]),
        ("end", out["b"]),
    ):  # the view's centre is the canvas centre at both ends
        assert out[key]["x"] + pose["cx"] * out[key]["scale"] == pytest.approx(960)
        assert out[key]["y"] + pose["cy"] * out[key]["scale"] == pytest.approx(540)
    assert out["mid"]["scale"] == pytest.approx(
        math.sqrt(out["a"]["s"] * out["b"]["s"])
    )  # an even zoom, not a linear one


@needs_node
def test_a_point_in_a_polygon():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    points = [[5, 5], [11, 5], [-1, 5], [5, 10.5]]
    got = run_lib(
        "input.pts.map(p => lib.insidePolygon(p[0], p[1], input.poly))",
        {"poly": square, "pts": points},
    )

    assert got == [True, False, False, False]


@needs_node
def test_the_spline_passes_through_its_points_and_the_graticule_follows_the_span():
    d = run_lib(
        "lib.splinePath(input)",
        [{"x": 0, "y": 0}, {"x": 100, "y": 50}, {"x": 200, "y": 0}],
    )

    assert d.startswith("M0.0 0.0 C") and d.count("C") == 2
    assert d.endswith("200.0 0.0") and "100.0 50.0" in d
    projection = maps.region("germany").projection.as_json()
    for span, step in ((8.1, 2), (24.7, 5), (3.0, 0.5)):
        grid = run_lib(
            "lib.graticulePath(lib.makeProjection(input.projection), input.window, input.span)",
            {"projection": projection, "window": [-4, 45, 24, 57], "span": span},
        )
        meridians = math.floor(24 / step) - math.ceil(-4 / step) + 1
        parallels = math.floor(57 / step) - math.ceil(45 / step) + 1
        assert grid.count("M") == meridians + parallels, span


# -- label measuring --------------------------------------------------------------------------------------------------


@needs_node
def test_the_advance_tables_are_the_font_files_widths():
    ttfont = pytest.importorskip("fontTools.ttLib")
    tables = run_lib("({label: lib.ADVANCE_LABEL, display: lib.ADVANCE_DISPLAY})")
    for key, file, last in (
        ("label", "IBMPlexSansCondensed-SemiBold.ttf", 0x17E),
        ("display", "Newsreader-Variable.ttf", 0x7E),
    ):
        font = ttfont.TTFont(tokens.FONTS_DIR / file)
        cmap, hmtx, table = font.getBestCmap(), font["hmtx"], tables[key]
        assert table["upm"] == font["head"].unitsPerEm
        for offset, advance in enumerate(table["w"]):
            glyph = cmap.get(table["first"] + offset)
            assert advance == (hmtx[glyph][0] if glyph else 0), (
                key,
                hex(table["first"] + offset),
            )
        assert table["first"] + len(table["w"]) - 1 == last


@needs_node
def test_a_measured_width_holds_the_text_and_wastes_little():
    ttfont = pytest.importorskip("fontTools.ttLib")
    font = ttfont.TTFont(tokens.FONTS_DIR / "IBMPlexSansCondensed-SemiBold.ttf")
    cmap, hmtx, upm = font.getBestCmap(), font["hmtx"], font["head"].unitsPerEm
    samples = [
        "Berlin",
        "Marburg",
        "Frankfurt am Main",
        "Gießen",
        "Würzburg",
        "Leipzig",
        "St. Petersburg",
        "Zürich",
    ]
    got = run_lib("input.map(s => lib.textWidth(s, 52, lib.ADVANCE_LABEL, 0))", samples)

    for text, width in zip(samples, got, strict=True):
        actual = sum(hmtx[cmap[ord(c)]][0] for c in text) * 52 / upm
        assert actual <= width <= actual * 1.06, text


@needs_node
def test_text_wraps_greedily_inside_the_width_and_never_loses_a_word():
    text = "The sealed train leaves Berlin for the mine at Marburg on the night of the eleventh"
    lines = run_lib("lib.wrapLines(input.text, 52, lib.ADVANCE_LABEL, 780)", {"text": text})
    widths = run_lib(
        "input.lines.map(l => lib.textWidth(l, 52, lib.ADVANCE_LABEL, 0))",
        {"lines": lines},
    )

    assert " ".join(lines) == text
    assert all(w <= 780 for w in widths) and len(lines) > 1
    assert run_lib("lib.wrapLines('', 52, lib.ADVANCE_LABEL, 780)") == []


# -- label placement --------------------------------------------------------------------------------------------------


def place(items, hard=(), soft=(), gap=14, pad=8):
    return run_lib(
        "lib.placeLabels(input.items, input.hard, input.soft, input.safe, input.gap, input.pad)",
        {
            "items": items,
            "hard": list(hard),
            "soft": list(soft),
            "safe": SAFE,
            "gap": gap,
            "pad": pad,
        },
    )


def item(pin_id, x, y, side="right", w=160, h=60, r=11, region=False):
    return {
        "id": pin_id,
        "x": x,
        "y": y,
        "side": side,
        "w": w,
        "h": h,
        "r": r,
        "region": region,
    }


def dot(x, y, r=11):
    return {"x0": x - r, "y0": y - r, "x1": x + r, "y1": y + r}


@needs_node
def test_a_label_takes_the_side_its_hint_names_when_that_side_is_free():
    for side in ("right", "left", "top", "bottom"):
        assert place([item("a", 900, 500, side)], [dot(900, 500)])["a"]["side"] == side


@needs_node
def test_a_blocked_hint_flips_to_the_opposite_side_then_to_above_then_below():
    me = dot(900, 500)
    right = dot(900 + 11 + 14 + 80, 500)  # a dot in the middle of the right-hand box
    left = dot(900 - 11 - 14 - 80, 500)
    above = dot(900, 500 - 11 - 14 - 30)

    assert place([item("a", 900, 500, "right")], [me, right])["a"]["side"] == "left"
    assert place([item("a", 900, 500, "right")], [me, right, left])["a"]["side"] == "top"
    assert place([item("a", 900, 500, "right")], [me, right, left, above])["a"]["side"] == "bottom"
    assert place([item("a", 900, 500, "top")], [me, above])["a"]["side"] == "bottom"


@needs_node
def test_a_label_stays_off_the_route_when_a_side_without_it_is_free_else_takes_a_crossed_one():
    east = [[x, 500] for x in range(960, 1100, 10)]  # a line to the right of the dot
    around = [
        *east,
        *[[x, 500] for x in range(700, 880, 10)],
        *[[900, y] for y in range(380, 480, 10)],
        *[[900, y] for y in range(525, 640, 10)],
    ]

    assert place([item("a", 900, 500, "right")], [dot(900, 500)], east)["a"]["side"] == "left"
    assert place([item("a", 900, 500, "right")], [dot(900, 500)], around)["a"]["side"] == "right"  # all cross: the hint


@needs_node
def test_labels_never_overlap_each_other_and_the_first_placed_keeps_its_side():
    items = [
        item("f", 900, 500, "right", w=240, h=74, r=16),
        item("b", 900, 560, "right"),
        item("c", 905, 620, "right"),
    ]
    got = place(items, [dot(900, 500, 16), dot(900, 560), dot(905, 620)])
    boxes = [got[k]["box"] for k in ("f", "b", "c")]

    assert got["f"]["side"] == "right"
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a["x1"] <= b["x0"] or b["x1"] <= a["x0"] or a["y1"] <= b["y0"] or b["y1"] <= a["y0"]


@needs_node
def test_a_label_never_leaves_the_safe_zone():
    got = place(
        [item("a", 140, 100, "left"), item("b", 1790, 990, "right")],
        [dot(140, 100), dot(1790, 990)],
    )

    for box in (got["a"]["box"], got["b"]["box"]):
        assert in_safe(box)
    assert got["a"]["side"] != "left" and got["b"]["side"] != "right"


@needs_node
def test_no_free_side_is_an_error_that_names_the_pin_and_a_region_label_has_one_place():
    around = [
        dot(900 + 105, 500),
        dot(900 - 105, 500),
        dot(900, 500 - 55),
        dot(900, 500 + 55),
    ]
    error = place([item("zed", 900, 500, "right")], [dot(900, 500), *around])
    region = place([item("th", 900, 500, "left", w=400, r=0, region=True)])
    covered = place(
        [item("th", 900, 500, "left", w=400, r=0, region=True)],
        [{"x0": 880, "y0": 480, "x1": 920, "y1": 520}],
    )

    assert "no room for the label of pin zed" in error["error"]
    assert region["th"]["side"] == "centre"
    assert region["th"]["box"]["x0"] == pytest.approx(700) and region["th"]["box"]["x1"] == pytest.approx(1100)
    assert "no room" in covered["error"]


@needs_node
def test_placement_is_deterministic_and_never_overlaps_on_seeded_scenes():
    rng = random.Random(20261005)
    scenes = []
    for _ in range(300):
        pins = [
            item(
                f"p{i}", rng.uniform(150, 1770), rng.uniform(100, 980), rng.choice(["right", "left", "top", "bottom"]),
                w=rng.uniform(100, 300), h=rng.choice([60, 74]), r=rng.choice([11, 16]),
            )
            for i in range(rng.randint(1, 6))
        ]  # fmt: skip
        corner = {
            "x0": 96,
            "y0": 54,
            "x1": 96 + rng.uniform(300, 700),
            "y1": 54 + rng.uniform(100, 260),
        }
        scenes.append(
            {
                "items": pins,
                "hard": [dot(p["x"], p["y"], p["r"]) for p in pins] + [corner],
                "soft": [],
            }
        )
    expression = (
        "input.map(s => { try { return lib.placeLabels(s.items, s.hard, s.soft, "
        f"{json.dumps(SAFE)}, 14, 8); }} catch (e) {{ return {{error: e.message}}; }} }})"
    )
    first, second = run_lib(expression, scenes), run_lib(expression, scenes)

    assert first == second
    placed = 0
    for scene, result in zip(scenes, first, strict=True):
        if "error" in result:
            assert "no room for the label" in result["error"]
            continue
        placed += 1
        boxes = [result[p["id"]]["box"] for p in scene["items"]]
        for i, box in enumerate(boxes):
            assert in_safe(box)
            assert all(apart(box, other, 8) for other in scene["hard"])
            assert all(apart(box, other, 8) for other in boxes[i + 1 :])
    assert placed > 200  # the scenes are crowded on purpose, but most fit


# -- reading the variables --------------------------------------------------------------------------------------------

CITIES = {
    "Berlin": {"name": "Berlin", "lon": 13.4, "lat": 52.5, "pop": 3400000},
    "Nurnberg": {"name": "Nürnberg", "lon": 11.1, "lat": 49.4, "pop": 700000},
}


def read_pins(pins, cities=CITIES):
    return run_lib(
        "lib.readPins(input.raw, input.cities, 'germany')",
        {"raw": pins, "cities": cities},
    )


@needs_node
def test_pins_come_as_json_text_or_as_the_list_and_a_name_is_looked_up_in_the_cities_table():
    pin = {
        "id": "berlin",
        "label": "Berlin",
        "lon": 13.4,
        "lat": 52.5,
        "kind": "focus",
        "labelSide": "left",
    }
    got = read_pins(json.dumps([pin]))

    assert got == [
        {
            "id": "berlin",
            "kind": "focus",
            "label": "Berlin",
            "side": "left",
            "lon": 13.4,
            "lat": 52.5,
        }
    ]
    assert read_pins([{"id": "berlin", "label": "Berlin", "lon": 13.4, "lat": 52.5}]) == [
        {**got[0], "kind": "city", "side": "right"}
    ]
    found = read_pins([{"id": "NURNBERG"}, {"id": "Berlin", "label": "  Hauptstadt "}])
    assert found[0] == {
        "id": "NURNBERG",
        "kind": "city",
        "label": "Nürnberg",
        "side": "right",
        "lon": 11.1,
        "lat": 49.4,
    }
    assert found[1]["label"] == "Hauptstadt"
    assert read_pins([{"id": "v", "kind": "via", "lon": 1, "lat": 2}])[0]["label"] == ""


@needs_node
@pytest.mark.parametrize(
    ("pins", "message"),
    [
        ("[", "pins is not valid JSON"),
        ([], "at least one pin"),
        ([{"label": "x", "lon": 1, "lat": 2}], "pins[0] needs an id"),
        (
            [
                {"id": "a", "label": "A", "lon": 1, "lat": 2},
                {"id": "a", "label": "B", "lon": 1, "lat": 2},
            ],
            "appears twice",
        ),
        (
            [{"id": "a", "label": "A", "lon": 1, "lat": 2, "kind": "big"}],
            "kind must be city, focus, region or via",
        ),
        ([{"id": "Atlantis"}], "not in the cities table of germany"),
        (
            [{"id": "a", "label": "A", "lon": "1", "lat": 2}],
            "needs numbers lon and lat",
        ),
        ([{"id": "a", "lon": 1, "lat": 2}], "needs a label"),
        (
            [{"id": "a", "label": "A", "lon": 1, "lat": 2, "labelSide": "up"}],
            "labelSide must be right, left, top or bottom",
        ),
    ],
)
def test_a_bad_pin_list_throws_a_message_that_names_the_problem(pins, message):
    assert message in read_pins(pins)["error"]


@needs_node
def test_at_most_six_pins_with_a_dot_or_a_label_but_any_number_of_route_points():
    many = [{"id": f"p{i}", "label": f"P{i}", "lon": i, "lat": i} for i in range(7)]
    vias = [{"id": f"v{i}", "kind": "via", "lon": i, "lat": i} for i in range(12)]

    assert "at most 6 pins" in read_pins(many)["error"]
    assert len(read_pins(many[:6] + vias)) == 18
    assert run_lib("lib.MAX_PINS") == T.limits["max_simultaneous_moving"]  # one pin is one thing moving


@needs_node
def test_the_route_names_pins_has_two_or_more_and_may_be_empty():
    def read_route(raw):
        return run_lib(
            "lib.readRoute(input.raw, input.pins)",
            {"raw": raw, "pins": [{"id": "a"}, {"id": "b"}, {"id": "c"}]},
        )

    assert read_route('["a","b","c"]') == ["a", "b", "c"]
    assert read_route("[]") == [] and read_route(["b", "a"]) == ["b", "a"]
    assert "at least two pins" in read_route('["a"]')["error"]
    assert "names z, which is not a pin" in read_route('["a","z"]')["error"]
    assert "route must be a list" in read_route('{"a":1}')["error"]


@needs_node
def test_a_camera_needs_numbers_and_a_span_between_zero_and_360():
    def read_camera(raw, name="cameraTo"):
        return run_lib("lib.readCamera(input.name, input.raw)", {"name": name, "raw": raw})

    assert read_camera('{"lon":10,"lat":51,"span":8}') == {
        "lon": 10,
        "lat": 51,
        "span": 8,
    }
    assert "cameraTo needs numbers lon, lat and span" in read_camera('{"lon":10,"lat":51}')["error"]
    assert (
        "cameraFrom.span must be between 0 and 360"
        in read_camera({"lon": 10, "lat": 51, "span": 0}, "cameraFrom")["error"]
    )
    assert "span must be between" in read_camera({"lon": 10, "lat": 51, "span": 400})["error"]


# -- the corner -------------------------------------------------------------------------------------------------------

SIZES = {
    "year": T.sizes["title"],
    "label": T.sizes["label"],
    "captionWidth": 780,
    "gap": 12,
}


def corner(year, caption, dots=(), route=()):
    return run_lib(
        "lib.cornerLayout(input.year, input.caption, input.sizes, input.safe, input.dots, input.route, 8)",
        {
            "year": year,
            "caption": caption,
            "sizes": SIZES,
            "safe": SAFE,
            "dots": list(dots),
            "route": list(route),
        },
    )


@needs_node
def test_the_date_and_caption_take_the_top_left_corner_unless_something_is_there():
    left = corner("February 11", "Berlin to Marburg by rail")

    assert left["side"] == "left" and left["box"]["x0"] == 96 and left["box"]["y0"] == 54
    assert 96 + 300 < left["box"]["x1"] < 96 + 780  # as wide as its widest line
    assert left["box"]["y1"] > 54 + 96 * 1.1 + 12 + 52  # the date, a gap and a line of caption
    assert corner("1945", "", dots=[dot(200, 100)])["side"] == "right"
    assert corner("1945", "", dots=[dot(200, 100)])["box"]["x1"] == SAFE["x1"]
    assert corner("1945", "", route=[[300, 120]])["side"] == "right"
    assert corner("", "") is None
    assert "both top corners are taken" in corner("1945", "", dots=[dot(200, 100), dot(1700, 100)])["error"]


@needs_node
def test_a_long_caption_wraps_so_the_box_grows_down_not_out():
    short = corner("", "Short line")["box"]
    text = "A long caption that has to go over several lines because it is much wider than the corner may be"
    long = corner("", text)["box"]

    assert long["x1"] - long["x0"] <= 781
    assert long["y1"] - long["y0"] > 2 * (short["y1"] - short["y0"])


@needs_node
def test_a_date_wider_than_the_corner_wraps_as_the_browser_wraps_it():
    one = corner("Spring 1945", "")["box"]
    two = corner("Spring 1945 to winter 1946", "")["box"]

    assert one["y1"] - one["y0"] == pytest.approx(96 * 1.1)
    assert two["y1"] - two["y0"] == pytest.approx(2 * 96 * 1.1)
    assert two["x1"] - two["x0"] <= 780 * 1.06 + 1


# -- the schedule -----------------------------------------------------------------------------------------------------

TOKEN_TIMES = {
    "enter": T.duration["enter"], "exit": T.duration["exit"], "micro": T.duration["micro"],
    "staggerTotal": T.duration["group_stagger_total_max"], "cinematicMin": T.duration["cinematic_min"],
    "cinematicMax": T.duration["cinematic_max"], "readMin": T.duration["read_min"], "readPerWord": T.duration["read_per_word"],
}  # fmt: skip
POSE = {"cx": 0, "cy": 0, "s": 3.0}


def plan(shown, route_length=500, year="", caption="", locked=True, pose_to=POSE):
    return run_lib(
        "lib.planTimeline(input)",
        {
            "t": TOKEN_TIMES, "width": 1920, "locked": locked, "poseFrom": POSE, "poseTo": pose_to,
            "shown": shown, "routeLength": route_length, "yearLabel": year, "caption": caption,
        },
    )  # fmt: skip


def pins_of(n, kind="city"):
    return [{"id": f"p{i}", "kind": kind, "label": f"Town {i}"} for i in range(n)]


@needs_node
def test_the_camera_moves_first_and_nothing_else_moves_until_it_is_held():
    locked = plan(pins_of(3))
    zoomed = {"cx": 100, "cy": 0, "s": 3.0 * math.e}
    moving = plan(pins_of(3), locked=False, pose_to=zoomed)

    assert locked["moveSeconds"] == 0 and locked["arrivals"][0]["at"] == T.duration["enter"]
    assert moving["moveSeconds"] == pytest.approx(2.0 + 1.0 + 300 * math.e / 1920)  # 2 s, a unit of log zoom, the pan
    assert all(a["at"] >= moving["moveSeconds"] for a in moving["arrivals"])
    assert (
        plan(
            pins_of(1),
            locked=False,
            pose_to={"cx": 0, "cy": 0, "s": 3.0 * math.exp(20)},
        )["moveSeconds"]
        == 4
    )


@needs_node
def test_pins_stagger_by_at_most_half_a_second_in_all_and_a_label_follows_its_dot():
    for count in range(1, 7):
        arrivals = plan(pins_of(count))["arrivals"]
        starts = [a["at"] for a in arrivals]

        assert starts[-1] - starts[0] <= T.duration["group_stagger_total_max"] + 1e-9
        assert starts == sorted(starts)
        assert all(a["labelAt"] == pytest.approx(a["at"] + 0.2) for a in arrivals)
    region = plan([{"id": "r", "kind": "region", "label": "Thuringia"}])["arrivals"][0]
    assert region["labelAt"] == region["at"]  # a region label has no dot to wait for


@needs_node
def test_route_date_and_caption_follow_the_pins_one_after_another():
    got = plan(pins_of(3), route_length=900, year="February 11", caption="By rail")

    assert got["routeAt"] == got["pinsEnd"] == got["yearAt"]
    assert got["routeSeconds"] == pytest.approx(900 / 700)
    assert got["captionAt"] == pytest.approx(got["yearAt"] + 0.25)  # a line stagger, not more
    assert got["entranceEnd"] == pytest.approx(got["pinsEnd"] + got["routeSeconds"])
    assert plan(pins_of(2), route_length=100)["routeSeconds"] == T.duration["cinematic_min"]
    assert plan(pins_of(2), route_length=5000)["routeSeconds"] == T.duration["cinematic_max"]
    assert plan(pins_of(2), route_length=0)["routeSeconds"] == 0


@needs_node
def test_the_hold_is_the_longer_of_1_2_s_and_0_3_s_a_word_and_the_slot_has_to_hold_it():
    few = plan(pins_of(1))
    many = plan(
        pins_of(3),
        year="February 11",
        caption="One line of ten words, more or less, and then a few",
    )

    assert few["hold"] == T.duration["read_min"]
    assert many["hold"] == pytest.approx(T.duration["read_per_word"] * (3 * 2 + 2 + 12))
    assert many["needed"] == pytest.approx(many["entranceEnd"] + many["hold"] + T.duration["exit"])
    fits = run_lib("lib.slotProblem(input.plan, input.d, 0.5)", {"plan": many, "d": many["needed"]})
    short = run_lib(
        "lib.slotProblem(input.plan, input.d, 0.5)",
        {"plan": many, "d": many["needed"] - 0.1},
    )
    assert fits == "" and "is shorter than the" in short and f"{many['needed']:.2f} s" in short
    assert run_lib("lib.slotProblem(input.plan, 'x', 0.5)", {"plan": many}).startswith("map-route: durationSeconds x")
