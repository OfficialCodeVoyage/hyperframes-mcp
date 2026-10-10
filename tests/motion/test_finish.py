"""The ``finish`` block and its three textures: the contract, the textures themselves and the stepping, run in node."""

from __future__ import annotations

import re
from functools import partial
from itertools import combinations, pairwise

import numpy as np
import pytest
from PIL import Image, ImageColor

from hyperframes_mcp.motion import lint, registry, tokens
from tests.motion.block_runner import (
    code_of,
    declared_variables,
    default_variables,
    needs_node,
    read_block,
    run_block,
)

BLOCK = tokens.MOTION_DIR / "blocks" / "finish.html"
TEXTURES = tokens.MOTION_DIR / "textures"
html = partial(read_block, BLOCK)
declared = partial(declared_variables, BLOCK)
defaults = partial(default_variables, BLOCK)


def run(variables=None, after="return null;", **options):
    # the page's CSS starts the overlay and every tile at opacity 0 (asserted in the style test below)
    initial = {".dust": {"opacity": 0}, ".tile": {"opacity": 0}}
    return run_block(
        BLOCK,
        variables,
        after=after,
        root="#finish-root",
        counts={".tile": 3},
        initial=initial,
        **options,
    )


def test_the_css_starts_the_overlay_and_every_tile_invisible_as_the_script_runs_assume():
    css = re.search(r"<style>(.*?)</style>", html(), re.DOTALL).group(1)

    for selector in ("#finish-root .dust", "#finish-root .tile"):
        rule = re.search(re.escape(selector) + r" \{([^}]*)\}", css).group(1)
        assert "opacity: 0;" in rule, selector


# -- the block, as far as a static test can see it -----------------------------------------------


def test_it_passes_the_linter():
    assert lint.lint_file(BLOCK) == []


def test_it_registers_one_timeline_under_its_name_with_no_blend_filter_or_shadow():
    text = code_of(html())

    assert text.count("gsap.timeline(") == 1
    assert 'window.__timelines["finish"] = tl;' in text
    assert not re.search(r"\.(?:to|from)\(", text)
    assert not re.search(r"filter|blur|mix-blend-mode|box-shadow", text)


def test_the_overlay_only_ever_changes_opacity_and_the_tiles_step_with_discrete_sets():
    text = code_of(html())
    script = re.search(r"<script>.*", text, re.DOTALL).group()

    assert not re.search(r"\b(?:scale|x|y|rotation|clipPath)\s*:", script)
    assert script.count("tl.set(") == 3
    assert "STEPS_PER_SECOND = 12" in script
    assert text.count(".fromTo(") == 2
    assert "immediateRender: false" in script
    # the fades use the dissolve time and the move ease of the tokens
    assert 'token("--dur-dissolve-min")' in script
    assert 'token("--ease-move")' in script


def test_the_three_textures_it_needs_are_a_component_it_depends_on_and_install_with_it(
    tmp_path,
):
    item = registry.get("finish")
    textures = registry.get("finish-textures")
    targets = {e.target: e for e in textures.files}
    in_html = re.findall(r'<img class="tile" alt="" src="([^"]+)"', html())

    assert item.files[0].path == "blocks/finish.html"
    assert item.registry_dependencies == ("finish-textures",)
    assert textures.type == "hyperframes:component" and textures.license == "CC0-1.0"
    assert len(in_html) == 3 and set(in_html) == set(targets)
    for src in in_html:
        assert targets[src].type == "hyperframes:asset"
        assert (tokens.MOTION_DIR / targets[src].path).is_file()
    # installing the block writes the tiles at the paths its markup names
    registry.install_item("finish", tmp_path)
    for src in in_html:
        assert (tmp_path / src).read_bytes() == (tokens.MOTION_DIR / targets[src].path).read_bytes()


def test_the_variables_are_declared_in_order_and_the_dust_defaults_to_low_opacity():
    assert [v["id"] for v in declared()] == ["durationSeconds", "dustOpacity"]
    assert defaults()["durationSeconds"] == 10
    assert 0 < defaults()["dustOpacity"] <= 0.5


@needs_node
def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    """A mounted block's <html> is not in the page: there is no declaration to read, only getVariables()."""
    out = run(None, "return tl.duration();", mounted=True)
    quiet = run(
        {"dustOpacity": 0.1, "durationSeconds": 3},
        "return tl.duration();",
        mounted=True,
    )

    assert out["error"] is None and out["result"] == 10
    assert quiet["error"] is None and quiet["result"] == 3


# -- the textures --------------------------------------------------------------------------------


def test_every_texture_is_a_transparent_tile_in_the_paper_and_ink_colours_under_a_megabyte():
    t = tokens.default_tokens()
    allowed = {ImageColor.getrgb(t.colors[name]) for name in ("paper", "ink")}
    pngs = sorted(TEXTURES.glob("*.png"))

    assert [p.name for p in pngs] == [
        "finish-dust-a.png",
        "finish-dust-b.png",
        "finish-dust-c.png",
    ]
    for path in pngs:
        assert path.stat().st_size <= 1_000_000, path.name
        with Image.open(path) as image:
            assert image.size == (1920, 1080) and image.mode == "RGBA", path.name
            data = np.asarray(image)
        marked = data[..., 3] > 0
        # sparse: a few percent of the frame, never a veil
        assert 0.005 < marked.mean() < 0.10, path.name
        colours = {tuple(int(c) for c in rgb) for rgb in np.unique(data[marked][:, :3], axis=0)}
        assert colours <= allowed, (path.name, colours)


def test_the_three_tiles_are_different_pictures():
    arrays = []
    for name in "abc":
        with Image.open(TEXTURES / f"finish-dust-{name}.png") as image:
            arrays.append(np.asarray(image)[..., 3] > 0)

    for a, b in combinations(arrays, 2):
        assert (a & b).sum() / a.sum() < 0.35


def test_textures_md_lists_every_file_with_its_exact_size_and_names_the_licence():
    notes = (TEXTURES / "TEXTURES.md").read_text(encoding="utf-8")
    third_party = (tokens.MOTION_DIR / "THIRD_PARTY.md").read_text(encoding="utf-8")

    for path in sorted(TEXTURES.glob("*.png")):
        row = next(line for line in notes.splitlines() if line.startswith("|") and f"`{path.name}`" in line)
        assert re.search(rf"\b{path.stat().st_size}\b", row), path.name
    for text in (notes, third_party):
        assert "CC0 1.0" in text
        assert "ambientCG" in text
        for asset in ("Scratches003", "SurfaceImperfections015"):
            assert asset in text
    assert "https://ambientcg.com/view?id=Scratches003" in third_party
    assert (TEXTURES / "make_tiles.py").is_file()


# -- the script, run in node ---------------------------------------------------------------------

STEPS = r"""
const tiles = els["all:.tile"], dust = els[".dust"];
const steps = Math.ceil(tl.duration() * 12);
const shown = [];
for (let k = 0; k < steps; k++) {
  tl.time((k + 0.5) / 12);
  const on = tiles.map((t, i) => (t.opacity === 1 ? i : -1)).filter((i) => i >= 0);
  shown.push(on);
}
const edges = [0, 0.25, 0.5, tl.duration() / 2, tl.duration() - 0.25, tl.duration()].map((t) => {
  tl.time(t); return [t, dust.opacity];
});
return { duration: tl.duration(), steps, shown, edges };
"""


@needs_node
def test_the_timeline_fills_the_slot_exactly_at_any_length():
    for seconds in (1, 3.5, 10, 37.5):
        out = run({"durationSeconds": seconds, "dustOpacity": 0.35}, "return tl.duration();")

        assert out["error"] is None
        assert out["result"] == pytest.approx(seconds, abs=1e-9), seconds


@needs_node
def test_exactly_one_tile_shows_at_every_step_and_never_the_same_one_twice_in_a_row():
    out = run({"durationSeconds": 20}, STEPS)["result"]
    shown = out["shown"]

    assert out["steps"] == 240
    assert all(len(on) == 1 for on in shown)
    tiles = [on[0] for on in shown]
    assert all(a != b for a, b in pairwise(tiles))
    # all three are used, and the order is not a plain 0, 1, 2 cycle
    assert set(tiles) == {0, 1, 2}
    assert tiles[:12] != [i % 3 for i in range(12)]


@needs_node
def test_the_order_is_the_same_on_every_run_and_when_the_timeline_is_seeked_out_of_order():
    first = run({"durationSeconds": 8}, STEPS)["result"]["shown"]
    second = run({"durationSeconds": 8}, STEPS)["result"]["shown"]
    shuffled = run(
        {"durationSeconds": 8},
        r"""
        const tiles = els["all:.tile"];
        const order = Array.from({ length: 96 }, (_, k) => (k * 37) % 96);
        const seen = {};
        for (const k of order) {
          tl.time((k + 0.5) / 12);
          seen[k] = tiles.map((t, i) => (t.opacity === 1 ? i : -1)).filter((i) => i >= 0);
        }
        return Array.from({ length: 96 }, (_, k) => seen[k]);
        """,
    )["result"]

    assert first == second == shuffled


@needs_node
def test_the_overlay_fades_in_and_out_over_the_dissolve_time_to_the_strength_asked():
    out = run({"durationSeconds": 10, "dustOpacity": 0.4}, STEPS)["result"]
    edges = {round(t, 3): o for t, o in out["edges"]}
    fade = tokens.default_tokens().duration["dissolve_min"]

    assert edges[0] == 0 and edges[10] == 0
    assert edges[5] == pytest.approx(0.4)
    assert 0 < edges[0.25] < 0.4  # still fading in
    assert 0 < edges[9.75] < 0.4  # already fading out
    assert fade == 0.5 and edges[0.5] == pytest.approx(0.4)


@needs_node
def test_a_strength_of_zero_is_allowed_and_a_wrong_one_or_a_short_slot_throws():
    assert run({"dustOpacity": 0})["error"] is None
    for bad in (1.5, -0.1, "strong", None):
        message = run({"dustOpacity": bad})["error"]
        assert message and "dustOpacity must be a number from 0 to 1" in message, bad
    short = run({"durationSeconds": 0.9})["error"]
    assert short and "is shorter than the 1 s" in short
