"""Vector graphics for a composed video: the strict check of an SVG, the vendored icon subset and its search, saving a
vector into a project with its record, and the local vectorizer (its library faked; the real call runs only when
``vtracer`` is installed)."""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

from hyperframes_mcp.motion import tokens, vectors
from hyperframes_mcp.motion.vectors import VectorError, check_svg

ROOT = Path(__file__).resolve().parents[2]
SVG = 'xmlns="http://www.w3.org/2000/svg"'
ICON = f'<svg {SVG} viewBox="0 0 24 24" fill="currentColor"><path d="M3 3h18v18H3z"/></svg>'


def refused(svg: str) -> str:
    """The message of the VectorError the check raises for ``svg`` (the test fails when there is none)."""
    with pytest.raises(VectorError) as caught:
        check_svg(svg)
    return str(caught.value)


def drawn(inner: str, root: str = f'viewBox="0 0 24 24" {SVG}') -> str:
    return f"<svg {root}>{inner}</svg>"


# -- the strict check -------------------------------------------------------------------------------


def test_a_plain_icon_passes_and_so_does_a_drawing_in_palette_colours_with_a_gradient_and_a_reuse():
    check_svg(ICON)
    check_svg(
        drawn(
            '<defs><linearGradient id="g"><stop offset="0" stop-color="#E6B94D"/>'
            '<stop offset="1" stop-color="#8c2f2a"/></linearGradient><path id="p" d="M0 0h4v4z"/></defs>'
            '<rect width="10" height="10" fill="url(#g)" stroke="currentColor"/>'
            '<use href="#p" fill="none"/>',
            f'viewBox="0 0 24 24" {SVG} xmlns:xlink="http://www.w3.org/1999/xlink"',
        )
    )


@pytest.mark.parametrize(
    ("svg", "words"),
    [
        ("<svg", "not valid XML"),
        (f"<html {SVG}/>", "root must be <svg"),
        (f'<svg {SVG}><path d="M0 0"/></svg>', "viewBox"),
        (f'<svg {SVG} viewBox="0 0 0 24"><path d="M0 0"/></svg>', "viewBox"),
        (drawn("<script>alert(1)</script>"), "<script> is not allowed"),
        (
            drawn("<foreignObject><div/></foreignObject>"),
            "<foreignObject> is not allowed",
        ),
        (drawn('<path d="M0 0" onclick="x()"/>'), "event attribute onclick"),
        (drawn('<path d="M0 0" onLoad="x()"/>'), "event attribute onLoad"),
        (
            drawn('<a href="https://example.com"><path d="M0 0"/></a>'),
            "<a> is not allowed",
        ),
        (drawn('<use href="https://example.com/x.svg#a"/>'), "href"),
        (
            drawn(
                '<use xlink:href="http://example.com/x.svg#a"/>',
                f'viewBox="0 0 24 24" {SVG} xmlns:xlink="http://www.w3.org/1999/xlink"',
            ),
            "href",
        ),
        (
            drawn('<image href="data:image/png;base64,AAAA" width="4" height="4"/>'),
            "<image> is not allowed",
        ),
        (drawn('<path d="M0 0" fill="url(https://example.com/a.svg#g)"/>'), "url("),
        (drawn('<path d="M0 0" style="fill:red"/>'), "style attribute"),
        (
            drawn("<style>@import url(https://example.com/a.css);</style>"),
            "<style> is not allowed",
        ),
        (drawn('<set attributeName="fill" to="red"/>'), "<set> is not allowed"),
        (drawn('<path d="M0 0" fill="url(#nowhere)"/>'), "no element has id nowhere"),
        (drawn('<path d="M0 0" data-x="javascript:alert(1)"/>'), "javascript:"),
        (
            drawn(
                '<path inkscape:label="x" d="M0 0"/>',
                f'viewBox="0 0 24 24" {SVG} xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"',
            ),
            "attribute",
        ),
        (
            drawn("<path d='M0 0'/>", f'viewBox="0 0 24 24" {SVG} xmlns:x="urn:other"').replace("<path", "<x:thing"),
            "not allowed",
        ),
    ],
)
def test_the_check_refuses_what_could_run_leave_the_file_or_hide_a_raster(svg, words):
    assert words in refused(svg)


def test_a_reuse_that_nests_is_refused_so_no_drawing_can_double_at_every_level():
    nested = drawn('<defs><g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g></defs><use href="#a"/>')
    itself = drawn('<use id="a" href="#a"/>')

    for svg in (nested, itself):
        assert "may not point at an element that holds another <use>" in refused(svg)
    check_svg(drawn('<defs><path id="p" d="M0 0h4v4z"/></defs><use href="#p"/><use href="#p"/>'))


def test_a_doctype_or_an_entity_is_refused_before_anything_is_parsed():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f'<svg {SVG} viewBox="0 0 1 1"><text>&b;</text></svg>'
    )

    assert "DOCTYPE" in refused(bomb)


def test_a_file_over_the_size_limit_or_with_too_many_elements_is_refused():
    big = drawn(f'<path d="M0 0{" h1" * 40_000}"/>')
    many = drawn('<circle r="1"/>' * vectors.MAX_ELEMENTS)

    assert len(big.encode()) > vectors.MAX_BYTES
    assert f"{vectors.MAX_BYTES // 1000} KB" in refused(big)
    assert f"{vectors.MAX_ELEMENTS} elements" in refused(many)
    check_svg(drawn('<circle r="1"/>' * (vectors.MAX_ELEMENTS - 1)))


@pytest.mark.parametrize("colour", ["red", "#ff0000", "#fff", "rgb(10,20,30)", "black", "#8C2F2B"])
def test_a_colour_outside_the_palette_is_refused_with_the_palette(colour):
    for attribute in ("fill", "stroke", "stop-color"):
        message = refused(drawn(f'<path d="M0 0" {attribute}="{colour}"/>'))

        assert f'{attribute}="{colour}"' in message
        assert "currentColor" in message
        for name, value in tokens.default_tokens().colors.items():
            assert f"{name} {value}" in message


@pytest.mark.parametrize("colour", ["currentColor", "none", "transparent", "#8c2f2a", "#8C2F2A", " #2f6f66 "])
def test_currentcolor_none_and_the_palette_in_any_case_are_accepted(colour):
    check_svg(drawn(f'<path d="M0 0" fill="{colour}" stroke="{colour}"/>'))


def test_every_problem_is_reported_at_once_so_the_agent_fixes_them_in_one_go():
    message = refused(drawn('<script/><path d="M0 0" onclick="x()" fill="red"/>'))

    assert "<script>" in message and "onclick" in message and 'fill="red"' in message


# -- the icon subset --------------------------------------------------------------------------------


def manifest() -> dict:
    return json.loads((vectors.ICONS_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_the_subset_is_a_few_hundred_icons_of_three_sets_under_two_megabytes():
    data = manifest()
    folder = vectors.ICONS_DIR
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())

    assert set(data["sets"]) == {"maki", "tabler", "material-symbols"}
    assert 250 <= len(data["icons"]) <= 600
    assert size < 2_000_000
    assert {i["set"] for i in data["icons"]} == set(data["sets"])


def test_every_set_names_its_licence_text_its_upstream_version_and_the_commit_it_was_copied_at():
    expected = {"maki": "CC0-1.0", "tabler": "MIT", "material-symbols": "Apache-2.0"}
    signature = {
        "maki": "CC0 1.0 Universal",
        "tabler": "MIT License",
        "material-symbols": "Apache License",
    }
    for key, info in manifest()["sets"].items():
        text = (vectors.ICONS_DIR / info["licence_file"]).read_text(encoding="utf-8")

        assert info["licence"] == expected[key]
        assert signature[key] in text
        assert re.fullmatch(r"[0-9a-f]{40}", info["sha"]) and info["version"]
        assert info["source"].startswith("https://github.com/") and info["notice"]


def test_every_icon_has_its_file_tags_and_upstream_source_and_passes_the_strict_check():
    seen = set()
    for icon in manifest()["icons"]:
        key = (icon["set"], icon["name"])
        path = vectors.ICONS_DIR / icon["set"] / f"{icon['name']}.svg"

        assert key not in seen
        seen.add(key)
        assert icon["tags"] and icon["source"].endswith(".svg")
        check_svg(path.read_text(encoding="utf-8"))
    on_disk = {(p.parent.name, p.stem) for p in vectors.ICONS_DIR.rglob("*.svg")}
    assert on_disk == seen


def test_every_icon_paints_with_currentcolor_so_the_block_can_recolour_it():
    for path in vectors.ICONS_DIR.rglob("*.svg"):
        text = path.read_text(encoding="utf-8")

        assert "currentColor" in text, path
        assert not re.search(r'(?:fill|stroke)="#', text), path


def test_the_third_party_list_names_each_set_with_its_licence_and_commit():
    third_party = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    for info in manifest()["sets"].values():
        assert info["title"] in third_party
        assert info["sha"] in third_party
        assert info["licence"] in third_party


def test_search_matches_names_and_tags_and_each_hit_carries_its_set_licence_and_a_path_that_exists():
    hits = vectors.search_icons("bank")

    assert 0 < len(hits) <= 8
    assert {"maki", "tabler", "material-symbols"} <= {h.set for h in vectors.search_icons("bank", 40)}
    for hit in hits:
        assert hit.id == f"{hit.set}/{hit.name}"
        assert hit.licence in {"CC0-1.0", "MIT", "Apache-2.0"}
        assert hit.path.is_file() and hit.path.name == f"{hit.name}.svg"
        assert hit.tags
    assert hits[0].name.startswith("bank") or "bank" in hits[0].name


def test_a_name_beats_a_tag_and_every_word_of_the_query_must_match():
    first = vectors.search_icons("anchor")[0]
    both = vectors.search_icons("map pin")

    assert "anchor" in first.name
    assert both and all("map" in " ".join([h.name, *h.tags]) and "pin" in " ".join([h.name, *h.tags]) for h in both)
    assert vectors.search_icons("zzzz-no-such-thing") == []
    assert vectors.search_icons("   ") == []


def test_the_limit_is_kept_and_capped():
    assert len(vectors.search_icons("arrow", 3)) == 3
    assert len(vectors.search_icons("arrow", 500)) <= vectors.MAX_HITS
    assert vectors.search_icons("arrow", 0) == []


def test_a_search_in_a_folder_of_its_own_reads_that_folders_manifest(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "coin.svg").write_text(ICON)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "sets": {"demo": {"licence": "CC0-1.0", "title": "Demo", "notice": "n"}},
                "icons": [
                    {
                        "set": "demo",
                        "name": "coin",
                        "tags": ["money"],
                        "source": "coin.svg",
                    }
                ],
            }
        )
    )

    (hit,) = vectors.search_icons("money", icons_dir=tmp_path)

    assert (hit.id, hit.licence, hit.path) == (
        "demo/coin",
        "CC0-1.0",
        tmp_path / "demo" / "coin.svg",
    )


# -- saving -----------------------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path) -> Path:
    (tmp_path / "project").mkdir()
    return tmp_path / "project"


def test_a_checked_svg_is_saved_with_a_record_that_says_it_was_written_by_claude(
    project,
):
    saved = vectors.save_vector(project, ICON, "square")

    assert saved.path == "assets/media/vectors/square.svg"
    assert (project / saved.path).read_text(encoding="utf-8").strip() == ICON
    record = json.loads((project / f"{saved.path}.json").read_text(encoding="utf-8"))
    assert record["source"] == "claude-written"
    assert record["licence"] == "LicenseRef-claude-written"
    assert record["credit"] is None
    assert record["bytes"] == (project / saved.path).stat().st_size
    assert re.fullmatch(r"[0-9a-f]{64}", record["sha256"])


def test_an_icon_id_from_the_search_is_saved_with_its_set_licence_and_notice(project):
    saved = vectors.save_vector(project, "tabler/building-bank", "bank")

    record = json.loads((project / f"{saved.path}.json").read_text(encoding="utf-8"))
    assert (record["source"], record["licence"], record["icon"]) == (
        "tabler",
        "MIT",
        "tabler/building-bank",
    )
    assert "Tabler" in record["notice"] and record["credit"] is None
    assert (project / saved.path).read_text(encoding="utf-8") == (
        vectors.ICONS_DIR / "tabler" / "building-bank.svg"
    ).read_text(encoding="utf-8")


def test_an_unknown_icon_id_says_how_to_find_one(project):
    with pytest.raises(VectorError, match="search_icons"):
        vectors.save_vector(project, "tabler/no-such-icon", "x")


def test_nothing_is_written_when_the_check_refuses(project):
    with pytest.raises(VectorError, match="script"):
        vectors.save_vector(project, drawn("<script/>"), "bad")

    assert not (project / "assets").exists()


@pytest.mark.parametrize("name", ["", "../escape", "a/b", "a.svg", "-lead", "x" * 65, "has space"])
def test_a_name_is_letters_digits_dash_and_underscore_only(project, name):
    with pytest.raises(VectorError, match="name"):
        vectors.save_vector(project, ICON, name)

    assert not (project / "assets").exists()


def test_a_saved_name_is_never_overwritten(project):
    vectors.save_vector(project, ICON, "square")

    with pytest.raises(VectorError, match="already"):
        vectors.save_vector(project, ICON.replace("M3 3", "M4 4"), "square")

    assert "M3 3" in (project / "assets/media/vectors/square.svg").read_text(encoding="utf-8")


# -- vectorize --------------------------------------------------------------------------------------


def picture(project: Path, name: str = "seal.png", size=(2400, 1200), record: dict | None = None) -> str:
    path = project / "assets" / "media" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 255, 255, 0)).save(path)
    if record is not None:
        path.with_name(path.name + ".json").write_text(json.dumps(record))
    return f"assets/media/{name}"


class FakeTracer:
    """A stand-in for ``vtracer`` that records the call and gives back a fixed drawing.

    Its parameters are positional only, as the real library has to be called: vtracer 0.6.15 segfaults on a keyword
    argument, so a call with one fails here too.
    """

    def __init__(self, svg: str) -> None:
        self.svg, self.calls = svg, []

    def convert_raw_image_to_svg(
        self,
        data,
        img_format,
        colormode,
        hierarchical,
        mode,
        filter_speckle,
        color_precision,
        layer_difference,
        corner_threshold,
        length_threshold,
        max_iterations,
        splice_threshold,
        path_precision,
        /,
    ):
        size = Image.open(io.BytesIO(data)).size
        self.calls.append((size, img_format, colormode, filter_speckle, path_precision))
        return self.svg


@pytest.fixture
def tracer(monkeypatch):
    fake = FakeTracer(
        f'<?xml version="1.0" encoding="UTF-8"?><svg version="1.1" {SVG} width="1000" height="500">'
        '<path d="M0 0h1000v500z" fill="#FFFFFF" transform="translate(0,0)"/>'
        '<path d="M0 0h10v10z" fill="#000000" transform="translate(5,5)"/>'
        '<path d="M2 2h4v4z" fill="#8f2e28" transform="translate(1,1)"/></svg>'
    )
    monkeypatch.setitem(sys.modules, "vtracer", fake)
    return fake


def test_a_picture_becomes_a_one_colour_vector_the_block_can_recolour(project, tracer):
    saved = vectors.vectorize(project, picture(project), "seal")

    svg = (project / saved.path).read_text(encoding="utf-8")
    assert 'viewBox="0 0 1000 500"' in svg and "currentColor" in svg
    assert "#" not in svg and not svg.startswith("<?xml")
    check_svg(svg)
    ((size, form, mode, speckle, precision),) = tracer.calls
    assert max(size) <= vectors.TRACE_MAX_EDGE and size == (1000, 500)
    assert (form, mode, speckle, precision) == ("png", "binary", 8, 1)


def test_a_picture_can_keep_its_colours_snapped_to_the_nearest_palette_colour(project, tracer):
    saved = vectors.vectorize(project, picture(project), "seal-colour", colors="palette")

    svg = (project / saved.path).read_text(encoding="utf-8")
    assert tracer.calls[0][2] == "color"
    assert 'fill="#8C2F2A"' in svg  # #8f2e28 is nearest to oxblood
    assert 'fill="#1E1A16"' in svg  # black is nearest to ink (the scrim is a backing, never drawn)
    assert "FFFFFF" not in svg.upper() and svg.count("<path") == 2  # the white ground is not a shape
    check_svg(svg)


def test_the_record_names_the_picture_the_tool_and_carries_the_pictures_credit(project, tracer):
    source = picture(project, record={"credit": "Bundesarchiv, Bild 183", "licence": "CC-BY-SA-3.0"})

    saved = vectors.vectorize(project, source, "seal")

    record = json.loads((project / f"{saved.path}.json").read_text(encoding="utf-8"))
    assert record["source"] == "vectorized" and record["derived_from"] == source
    assert (record["credit"], record["licence"]) == (
        "Bundesarchiv, Bild 183",
        "CC-BY-SA-3.0",
    )
    assert record["tool"].startswith("vtracer")


def test_a_vectorized_picture_goes_through_the_same_check(project, tracer):
    tracer.svg = tracer.svg.replace('fill="#000000"', 'fill="#000000" onclick="x()"')

    with pytest.raises(VectorError, match="onclick"):
        vectors.vectorize(project, picture(project), "seal")

    assert not (project / "assets/media/vectors").exists()


def test_a_trace_too_big_for_the_limit_says_what_to_do(project, tracer):
    tracer.svg = tracer.svg.replace("</svg>", f'<path d="M0 0{" h1" * 40_000}"/></svg>')

    with pytest.raises(VectorError, match="KB"):
        vectors.vectorize(project, picture(project), "seal")


def test_without_vtracer_the_tool_says_how_to_install_it(project, monkeypatch):
    monkeypatch.setitem(sys.modules, "vtracer", None)

    with pytest.raises(VectorError, match=r"pip install 'vtracer>="):
        vectors.vectorize(project, picture(project), "seal")


@pytest.mark.parametrize("colors", ["rainbow", ""])
def test_an_unknown_colour_mode_is_refused_before_any_work(project, tracer, colors):
    with pytest.raises(VectorError, match="mono"):
        vectors.vectorize(project, picture(project), "seal", colors=colors)

    assert tracer.calls == []


def test_a_missing_or_unreadable_picture_is_an_error_not_a_crash(project, tracer):
    with pytest.raises(VectorError, match="no picture"):
        vectors.vectorize(project, "assets/media/none.png", "seal")
    (project / "broken.png").write_bytes(b"not a png")
    with pytest.raises(VectorError, match="cannot be read"):
        vectors.vectorize(project, "broken.png", "seal")


def test_the_real_tracer_makes_a_vector_of_a_drawn_shape_that_passes_the_check(project):
    pytest.importorskip("vtracer")
    image = Image.new("RGB", (400, 400), "white")
    from PIL import ImageDraw

    ImageDraw.Draw(image).ellipse((60, 60, 340, 340), fill="black")
    path = project / "disc.png"
    image.save(path)

    saved = vectors.vectorize(project, "disc.png", "disc")

    svg = (project / saved.path).read_text(encoding="utf-8")
    assert 'viewBox="0 0 400 400"' in svg and "currentColor" in svg
    check_svg(svg)
