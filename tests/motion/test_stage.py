"""``stage_block``: the scratch project of one block, as the render and the review frames rely on it."""

from __future__ import annotations

import html
import json
import re

import pytest

from hyperframes_mcp.motion import registry, tokens
from hyperframes_mcp.motion.stage import StageError, stage_block

TRICKY = "A \"quoted\" <b>& 'q'</b></script>"


def attributes(index_html: str, element_id: str) -> dict[str, str]:
    """The attributes of the element with this id, HTML-unescaped the way a browser reads them."""
    tag = re.search(rf'<div\s+id="{element_id}"([^>]*)>', index_html, re.DOTALL).group(1)
    return {k: html.unescape(v) for k, v in re.findall(r'([\w-]+)="([^"]*)"', tag)}


def test_it_installs_the_block_and_what_it_needs(tmp_path):
    staged = stage_block("credit-line", None, tmp_path / "project")

    for relative in (
        "compositions/credit-line.html",
        "vendor/gsap.min.js",
        "tokens.css",
        "motion.lock.json",
        "assets/fonts/IBMPlexSansCondensed-Medium.ttf",
    ):
        assert (staged.project_dir / relative).is_file(), relative


def test_the_root_is_the_canvas_of_the_tokens_and_lasts_the_blocks_default_slot(
    tmp_path,
):
    canvas = tokens.default_tokens().canvas
    staged = stage_block("credit-line", None, tmp_path)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")

    root = attributes(text, "root")
    assert root["data-composition-id"] == "main"
    assert (root["data-width"], root["data-height"], root["data-fps"]) == (
        str(canvas["width"]),
        str(canvas["height"]),
        str(canvas["fps"]),
    )
    assert float(root["data-duration"]) == registry.get("credit-line").duration == 6.2
    assert staged.duration_s == staged.total_s == 6.2 and staged.start == 0.0


def test_the_block_is_mounted_by_source_with_its_slot_and_its_variables(tmp_path):
    stage_block("credit-line", {"text": "Bank of England", "align": "right"}, tmp_path)

    mount = attributes((tmp_path / "index.html").read_text(encoding="utf-8"), "block")

    assert mount["data-composition-id"] == "credit-line"
    assert mount["data-composition-src"] == "compositions/credit-line.html"
    assert (mount["data-width"], mount["data-height"]) == ("1920", "1080")
    assert float(mount["data-start"]) == 0.0 and float(mount["data-duration"]) == 6.2
    assert json.loads(mount["data-variable-values"]) == {
        "text": "Bank of England",
        "align": "right",
    }


def test_text_with_quotes_and_markup_stays_inside_the_attribute(tmp_path):
    stage_block("credit-line", {"text": TRICKY}, tmp_path)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")

    mount = attributes(text, "block")

    assert json.loads(mount["data-variable-values"]) == {"text": TRICKY}
    assert "<b>" not in text and "</b>" not in text
    # the page's own two scripts and none from the text
    assert text.count("</script>") == 2


def test_a_duration_in_the_variables_sets_the_slot_and_the_project_length(tmp_path):
    staged = stage_block("credit-line", {"durationSeconds": 9.5}, tmp_path)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert staged.duration_s == staged.total_s == 9.5
    assert float(attributes(text, "root")["data-duration"]) == 9.5
    assert float(attributes(text, "block")["data-duration"]) == 9.5
    assert json.loads(attributes(text, "block")["data-variable-values"]) == {"durationSeconds": 9.5}


def test_a_start_offset_moves_the_mount_and_lengthens_the_project(tmp_path):
    staged = stage_block("credit-line", {"durationSeconds": 8}, tmp_path, start=2.5)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")

    assert (staged.start, staged.duration_s, staged.total_s) == (2.5, 8.0, 10.5)
    assert float(attributes(text, "block")["data-start"]) == 2.5
    assert float(attributes(text, "root")["data-duration"]) == 10.5


def test_a_night_ground_lies_under_the_block(tmp_path):
    stage_block("credit-line", None, tmp_path)
    text = (tmp_path / "index.html").read_text(encoding="utf-8")
    night = tokens.default_tokens().colors["night"]

    assert text.index('id="ground"') < text.index('id="block"')
    assert re.search(rf"#ground \{{[^}}]*background: {night};", text)
    assert re.search(rf"html, body \{{[^}}]*background: {night};", text)


def test_the_project_is_recognised_as_one_by_the_cli(tmp_path):
    """HyperFrames takes a folder with index.html and one marker file for a project."""
    stage_block("credit-line", None, tmp_path)

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"id": "motion-credit-line", "name": "credit-line"}


def test_staging_twice_gives_the_same_files(tmp_path):
    stage_block("credit-line", {"text": "Same"}, tmp_path)
    first = (tmp_path / "index.html").read_text(encoding="utf-8")

    stage_block("credit-line", {"text": "Same"}, tmp_path)

    assert (tmp_path / "index.html").read_text(encoding="utf-8") == first


def test_an_unknown_block_says_what_exists(tmp_path):
    with pytest.raises(registry.RegistryError, match="credit-line"):
        stage_block("nope", None, tmp_path)


def test_an_undeclared_variable_is_an_error_and_names_the_declared_ones(tmp_path):
    with pytest.raises(StageError) as raised:
        stage_block("credit-line", {"txt": "typo"}, tmp_path)

    assert "txt" in str(raised.value)
    assert "durationSeconds, text, align" in str(raised.value)
    assert not (tmp_path / "index.html").exists()


@pytest.mark.parametrize("duration", [0, -3, "6", True, None])
def test_a_duration_that_is_not_a_positive_number_is_an_error(tmp_path, duration):
    with pytest.raises(StageError, match="durationSeconds"):
        stage_block("credit-line", {"durationSeconds": duration}, tmp_path)


def test_a_negative_start_is_an_error(tmp_path):
    with pytest.raises(StageError, match="start"):
        stage_block("credit-line", None, tmp_path, start=-1)


def test_a_component_is_not_a_block(tmp_path, monkeypatch):
    component = registry.Item(
        name="a-component",
        type="hyperframes:component",
        title="t",
        description="d",
        license="MIT",
        files=(),
        registry_dependencies=(),
        dimensions=None,
        duration=None,
        variables=(),
        source=None,
        notes="",
    )
    monkeypatch.setattr(registry, "get", lambda name, reg=None: component)

    with pytest.raises(StageError, match="not a block"):
        stage_block("a-component", None, tmp_path)
