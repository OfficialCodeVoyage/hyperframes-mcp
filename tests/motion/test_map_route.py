"""The ``map-route`` block: the contract a static test can see, its registry entries, and the three maps of the prototype.

``tests/motion/test_map_lib.py`` calls the helper library under node. What only a browser can say (the real width of a label,
where things land) is checked by the opt-in test at the end (``MOTION_BROWSER_TESTS=1``): it loads the installed block in the
headless Chrome of the pinned HyperFrames, once for the default and once for each of the three prototype maps.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

import pytest

from hyperframes_mcp.hfcli.cli import NODE_DIR, _find_browser
from hyperframes_mcp.motion import lint, maps, registry
from tests.motion.map_helpers import (
    BLOCK,
    LIB,
    MOTION,
    NODE,
    T,
    apart,
    code_of,
    declared,
    defaults,
    enum_options,
    html,
    in_safe,
    load_script,
    needs_node,
    run_lib,
)

CLOCK = r"Math\.random|Date\.now|performance\.now|new Date|requestAnimationFrame|setTimeout|setInterval"

# -- the contract, as far as a static test can see ---------------------------------------------------------------------


def test_the_block_and_the_whole_tree_pass_the_linter():
    assert lint.lint_file(BLOCK) == []
    assert lint.lint_tree(MOTION) == []


def test_it_registers_one_paused_timeline_under_its_composition_id():
    text = html()

    assert re.search(r'data-composition-id="([^"]+)"', text).group(1) == "map-route"
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert 'window.__timelines["map-route"] = tl;' in text


def test_it_only_uses_fromto_and_takes_every_colour_size_font_and_ease_from_the_tokens():
    text = code_of(html())
    without_ids = re.sub(r"url\(#[^)]*\)", "", re.sub(r'"#[^"]*"', "", text))

    assert ".fromTo(" in text
    assert not re.search(r"\.(?:to|from)\(", text)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", without_ids)
    assert not re.search(r"\b(?:rgba?|hsla?)\(", text)
    assert not re.search(r"font-size:\s*\d", text)
    assert not re.search(r"font-family:\s*[\"']", text)
    for name in (
        "--ease-enter",
        "--ease-exit",
        "--ease-move",
        "--dur-enter",
        "--dur-exit",
        "--dur-micro",
    ):
        assert f'"{name}"' in text
    assert not re.search(r"ease:\s*[\"'](?!none)", text)  # no literal ease but the baked camera segments


def test_the_linear_camera_segments_are_marked_and_nothing_else_is_linear():
    text = html()

    assert text.count('ease: "none"') == 1
    assert re.search(r'<g class="camera" data-linear-ok', text)


def test_it_tweens_only_what_the_contract_allows():
    text = code_of(html())
    calls = " ".join(re.findall(r"\.fromTo\((.*?)\);", text, re.DOTALL))
    keys = set(re.findall(r"[{,]\s*(\w+)\s*:", calls))
    allowed = {
        "x",
        "y",
        "scale",
        "opacity",
        "strokeDashoffset",
        "duration",
        "ease",
        "immediateRender",
    }

    assert "svgOrigin" in text  # set once, never tweened
    assert not keys - allowed, keys - allowed


def test_labels_are_at_label_size_the_date_at_title_size_and_figures_are_tabular():
    text = html()

    assert re.search(r"\.pin-label \{[^}]*font-size: var\(--size-label\)", text)
    assert re.search(
        r'\.pin-label\[data-kind="focus"\] \{[^}]*font-size: var\(--size-subtitle\)',
        text,
    )
    assert re.search(r"\.year \{[^}]*font-size: var\(--size-title\)", text)
    assert re.search(r"\.caption \{[^}]*font-size: var\(--size-label\)", text)
    assert "font-variant-numeric: tabular-nums" in text
    assert T.sizes["label"] >= T.sizes["min_text"]


def test_the_corner_numbers_of_the_library_are_the_ones_the_stylesheet_sets():
    """Line heights, the date's width and gap and the region label's letter spacing are written in CSS and measured in script."""
    text = html()
    lib = LIB.read_text(encoding="utf-8")

    assert re.search(r"--caption-width: 780px", text) and re.search(r"--corner-gap: 12px", text)
    assert re.search(r'captionWidth: number\("--caption-width"\)', text)
    assert re.search(r'gap: number\("--corner-gap"\)', text)
    for selector, factor in (
        (r"\.year", "1.1"),
        (r"\.caption", "1.25"),
        (r"\.pin-label", "1.15"),
    ):
        assert re.search(selector + r" \{[^}]*line-height: " + re.escape(factor) + ";", text), selector
    assert "sizes.year * 1.1" in lib and "sizes.label * 1.25" in lib and "size * 1.15" in text
    assert re.search(r"letter-spacing: 0\.32em", text) and "isRegion ? 0.32 : 0" in text


def test_the_advance_tables_belong_to_the_fonts_of_the_label_and_display_roles():
    assert "IBMPlexSansCondensed-SemiBold.ttf" in [f.file for f in T.fonts["label"].files]
    assert "Newsreader-Variable.ttf" in [f.file for f in T.fonts["display"].files]
    assert re.search(
        r"\.pin-label \{[^}]*font-family: var\(--font-label\);[^}]*font-weight: 600",
        html(),
    )
    assert re.search(r"\.year \{[^}]*font-family: var\(--font-display\)", html())


def test_it_has_no_drop_shadow_and_edge_contrast_comes_from_a_halo():
    text = html()

    assert "text-shadow" not in text and "box-shadow" not in text and "drop-shadow" not in text
    assert "paint-order: stroke fill" in text


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    text = code_of(html())

    assert "getComputedStyle(rootEl)" in text
    assert "getVariables" in text
    assert "getElementById" not in text
    assert 'document.querySelector("#map-root")' in text
    assert "document.documentElement.getAttribute(" in text


def test_it_loads_its_scripts_from_the_project_and_the_timeline_is_a_pure_function_of_the_variables():
    scripts = re.findall(r'<script src="([^"]+)"', html())
    lib = LIB.read_text(encoding="utf-8")

    assert scripts == [
        "vendor/gsap.min.js", "js/map-lib.js", "data/map-germany.js", "data/map-europe.js", "data/map-world.js",
    ]  # fmt: skip
    assert not re.search(CLOCK, code_of(html())) and not re.search(CLOCK, lib)
    assert not re.search(r"https?://", lib)


def test_a_slot_too_short_fails_the_render_and_the_fade_out_ends_with_the_slot():
    text = code_of(html())

    assert "lib.slotProblem(" in text and "throw new Error(problem)" in text
    assert "duration - t.exit" in text


# -- the registry entries ----------------------------------------------------------------------------------------------


def test_registry_entries_cover_the_block_the_kit_and_their_licences():
    block = registry.get("map-route")
    kit = registry.get("map-kit")

    assert block.type == "hyperframes:block" and kit.type == "hyperframes:component"
    assert block.registry_dependencies == ("map-kit",)
    assert {entry.path for entry in kit.files} == {
        "data/map-germany.js", "data/map-europe.js", "data/map-world.js", "js/map-lib.js",
    }  # fmt: skip
    assert kit.license == "LicenseRef-NaturalEarth-PublicDomain"
    assert block.license == "Apache-2.0"
    assert kit.source is not None and kit.source.sha == maps.NE_COMMIT and block.source is None
    assert [v["id"] for v in block.variables] == [
        "durationSeconds", "region", "pins", "route", "yearLabel", "caption", "cameraFrom", "cameraTo", "routeStyle", "borders",
    ]  # fmt: skip


def test_installing_the_block_brings_the_kit_the_tokens_and_the_fonts(tmp_path):
    registry.install_item("map-route", tmp_path)

    for path in (
        "compositions/map-route.html", "js/map-lib.js", "data/map-germany.js", "data/map-europe.js",
        "data/map-world.js", "tokens.css", "vendor/gsap.min.js", "assets/fonts/IBMPlexSansCondensed-SemiBold.ttf",
        "assets/fonts/Newsreader-Variable.ttf",
    ):  # fmt: skip
        assert (tmp_path / path).is_file(), path


def test_the_default_variables_are_valid_and_make_a_route_over_pins_that_exist():
    values = defaults()
    pin_ids = {p["id"] for p in json.loads(values["pins"])}
    assert values["region"] == "germany" and set(json.loads(values["route"])) <= pin_ids
    assert values["durationSeconds"] == float(re.search(r'data-duration="([\d.]+)"', html()).group(1))


# -- the three maps of the prototype as variable sets ------------------------------------------

RAIL = {
    "durationSeconds": 9.0,
    "region": "germany",
    "pins": json.dumps(
        [
            {"id": "Berlin", "labelSide": "right"},
            {"id": "Leipzig", "labelSide": "right"},
            {"id": "via1", "kind": "via", "lon": 12.55, "lat": 52.0},
            {"id": "via2", "kind": "via", "lon": 11.7, "lat": 51.45},
            {"id": "via3", "kind": "via", "lon": 10.95, "lat": 51.05},
            {
                "id": "marburg",
                "label": "Marburg",
                "kind": "focus",
                "lon": 10.12,
                "lat": 50.82,
                "labelSide": "left",
            },
        ]
    ),
    "route": json.dumps(["Berlin", "via1", "via2", "via3", "marburg"]),
    "yearLabel": "February 11",
    "caption": "",
    "cameraFrom": json.dumps({"lon": 11.75, "lat": 51.7, "span": 8.09}),
    "cameraTo": json.dumps({"lon": 11.75, "lat": 51.7, "span": 7.22}),
    "routeStyle": "rail",
    "borders": "none",
}
TRUCKS = {
    "durationSeconds": 9.0,
    "region": "germany",
    "pins": json.dumps(
        [
            {"id": "Frankfurt", "labelSide": "left"},
            {
                "id": "marburg",
                "label": "Marburg",
                "kind": "focus",
                "lon": 10.12,
                "lat": 50.82,
                "labelSide": "right",
            },
            {
                "id": "fulda",
                "label": "Fulda",
                "lon": 9.68,
                "lat": 50.55,
                "labelSide": "right",
            },
            {"id": "via1", "kind": "via", "lon": 9.92, "lat": 50.66},
            {"id": "via2", "kind": "via", "lon": 9.2, "lat": 50.34},
        ]
    ),
    "route": json.dumps(["marburg", "via1", "fulda", "via2", "Frankfurt"]),
    "yearLabel": "April 15",
    "caption": "",
    "cameraFrom": json.dumps({"lon": 9.4, "lat": 50.5, "span": 4.19}),
    "cameraTo": json.dumps({"lon": 9.4, "lat": 50.48, "span": 3.79}),
    "routeStyle": "dots",
    "borders": "none",
}
LOCATOR = {
    "durationSeconds": 9.0,
    "region": "germany",
    "pins": json.dumps(
        [
            {"id": "Erfurt", "labelSide": "right"},
            {
                "id": "marburg",
                "label": "Marburg",
                "kind": "focus",
                "lon": 10.12,
                "lat": 50.82,
                "labelSide": "left",
            },
            {
                "id": "thuringia",
                "label": "Thuringia",
                "kind": "region",
                "lon": 11.35,
                "lat": 51.3,
            },
        ]
    ),
    "route": "[]",
    "yearLabel": "",
    "caption": "",
    "cameraFrom": json.dumps({"lon": 9.6, "lat": 51.3, "span": 24.7}),
    "cameraTo": json.dumps({"lon": 10.45, "lat": 50.98, "span": 6.7}),
    "routeStyle": "line",
    "borders": "none",
}
SETS = {"default": {}, "rail": RAIL, "trucks": TRUCKS, "locator": LOCATOR}


def full(values: dict) -> dict:
    return {**defaults(), **values}


def test_each_set_names_only_declared_variables_with_valid_choices():
    ids = {v["id"] for v in declared()}

    for name, values in SETS.items():
        assert set(values) <= ids, name
        for key, allowed in enum_options().items():
            assert full(values)[key] in allowed, (name, key)


def test_the_sets_match_the_content_and_the_cameras_of_the_prototype():
    rail, trucks, locator = (json.loads(s["pins"]) for s in (RAIL, TRUCKS, LOCATOR))

    assert [p["id"] for p in rail if p.get("kind") != "via"] == [
        "Berlin",
        "Leipzig",
        "marburg",
    ]
    assert [p["id"] for p in trucks if p.get("kind") != "via"] == [
        "Frankfurt",
        "marburg",
        "fulda",
    ]
    assert [p["id"] for p in locator] == ["Erfurt", "marburg", "thuringia"] and locator[2]["kind"] == "region"
    assert (RAIL["yearLabel"], TRUCKS["yearLabel"], LOCATOR["yearLabel"]) == (
        "February 11",
        "April 15",
        "",
    )
    assert (RAIL["routeStyle"], TRUCKS["routeStyle"]) == ("rail", "dots")
    assert json.loads(RAIL["route"])[0] == "Berlin" and json.loads(RAIL["route"])[-1] == "marburg"
    assert json.loads(TRUCKS["route"])[0] == "marburg" and json.loads(TRUCKS["route"])[-1] == "Frankfurt"
    per_degree = 130 * math.cos(math.radians(51))  # map units of a degree of longitude, as the prototype
    for values, scales in (
        (RAIL, (2.9, 3.25)),
        (TRUCKS, (5.6, 6.2)),
        (LOCATOR, (0.95, 3.5)),
    ):
        for key, scale in zip(("cameraFrom", "cameraTo"), scales, strict=True):
            assert json.loads(values[key])["span"] == pytest.approx(1920 / (per_degree * scale), abs=0.06)


@needs_node
@pytest.mark.parametrize("name", ["default", "rail", "trucks", "locator"])
def test_every_set_keeps_its_pins_in_the_safe_zone_and_its_cameras_inside_the_map_data(
    name,
):
    values = full(SETS[name])
    data = load_script(values["region"])
    out = run_lib(
        "(() => { const p = lib.makeProjection(input.data.projection);"
        "const cams = [lib.readCamera('cameraFrom', input.v.cameraFrom), lib.readCamera('cameraTo', input.v.cameraTo)];"
        "const poses = cams.map(c => lib.cameraPose(p, c, 1920));"
        "const pins = lib.readPins(input.v.pins, input.data.cities, input.v.region);"
        "const route = lib.readRoute(input.v.route, pins);"
        "const screen = pins.map(q => { const xy = p(q.lon, q.lat);"
        "return {id: q.id, x: 960 + (xy[0] - poses[1].cx) * poses[1].s, y: 540 + (xy[1] - poses[1].cy) * poses[1].s}; });"
        "const inside = poses.map(o => [-1, 0, 1].every(i => [-1, 0, 1].every(j =>"
        " lib.insidePolygon(o.cx + i * 960 / o.s, o.cy + j * 540 / o.s, input.data.outline))));"
        "return {screen, inside, route}; })()",
        {"data": data, "v": values},
    )

    assert out["inside"] == [True, True]
    for pin in out["screen"]:
        assert in_safe({"x0": pin["x"], "x1": pin["x"], "y0": pin["y"], "y1": pin["y"]}), pin
    assert set(out["route"]) <= {p["id"] for p in out["screen"]}


# -- a real browser, opt in --------------------------------------------------------------------------------------------

PUPPETEER = Path(
    os.environ.get(
        "MOTION_PUPPETEER",
        NODE_DIR / "node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js",
    )
)


def find_chrome() -> Path | None:
    """The headless Chrome the animated scene renderer would use (MOTION_CHROME, SCENE_RENDER_BROWSER or the HyperFrames cache)."""
    return _find_browser(os.environ.get("MOTION_CHROME"))


@needs_node
@pytest.mark.skipif(
    os.environ.get("MOTION_BROWSER_TESTS") != "1" or find_chrome() is None or not PUPPETEER.is_file(),
    reason="set MOTION_BROWSER_TESTS=1 (needs a headless Chrome and puppeteer-core: MOTION_CHROME, MOTION_PUPPETEER)",
)
def test_in_a_browser_each_set_builds_its_timeline_and_its_text_neither_overlaps_nor_leaves_the_safe_zone(
    tmp_path,
):
    registry.install_item("map-route", tmp_path)
    driver = Path(__file__).with_name("map_route_browser.mjs")
    done = subprocess.run(
        [NODE, str(driver), str(tmp_path), str(find_chrome()), str(PUPPETEER)],
        input=json.dumps(SETS),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)

    for name, values in SETS.items():
        errors, result = report[name]["errors"], report[name]["result"]
        assert errors == [], (name, errors)
        assert result is not None, name
        assert result["duration"] == pytest.approx(full(values)["durationSeconds"]), name
        assert result["stageAtStart"] == 0 and result["stageAtEnd"] == 0, name
        texts = result["held"]["labels"] + result["held"]["corner"]
        assert texts, name
        boxes = [t["box"] for t in texts]
        for text in texts:
            box = text["box"]
            assert in_safe(box), (name, text["text"])
            assert text["size"] >= T.sizes["min_text"], (
                name,
                text["text"],
                text["size"],
            )
        for i, box in enumerate(boxes):
            assert all(apart(box, dot) for dot in result["held"]["dots"]), (
                name,
                texts[i]["text"],
            )
            assert all(apart(box, other) for other in boxes[i + 1 :]), (
                name,
                texts[i]["text"],
            )
