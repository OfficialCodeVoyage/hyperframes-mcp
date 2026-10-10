"""The vector tools as the HyperFrames app's agent calls them, through an in-memory MCP client: find an icon, save a
vector after the strict check, trace a picture (the tracer faked; its real call is in ``tests/motion/test_vectors.py``),
and add the vector to the video as a layer. Nothing here costs anything, so nothing is faked for the money's sake."""

from __future__ import annotations

import json
import sys

import pytest
from mcp import Client
from PIL import Image

from hyperframes_mcp.motion import hf_cli, icon_sheet, manifest
from hyperframes_mcp.server.server import build_server
from tests.motion.test_project_edit import composed, two_shots
from tests.server.test_tools import call, images, text

ARROW = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor">'
    '<path d="M3 12h18M15 6l6 6-6 6"/></svg>'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project 20 s long (p1 a photo, 0 to 8 s; m1 a clip, 8 to 16 s), the session's folder."""
    root = composed(tmp_path, two_shots())[0]
    manifest.write_manifest(root, manifest.ProjectManifest(profile="history"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def sheet(monkeypatch):
    """The icon sheet drawn by a fake (the real one runs a browser: ``tests/motion/test_icon_sheet.py``); the ids it was
    asked to draw, in order, for each call."""
    drawn = []

    def fake(icons, folder, **_):
        drawn.append([icon.id for icon in icons])
        out = folder / "icons.png"
        Image.new("RGB", (880, 448), "white").save(out)
        return out

    monkeypatch.setattr(icon_sheet, "icon_sheet", fake)
    return drawn


@pytest.mark.asyncio
async def test_the_four_tools_are_there_and_only_the_search_is_read_only(project):
    async with Client(build_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert tools["search_icons"].annotations.read_only_hint is True
    for name in ("save_vector", "vectorize", "add_graphic"):
        assert tools[name].annotations.read_only_hint is False, name
        assert tools[name].annotations.destructive_hint is False, name
        assert tools[name].annotations.open_world_hint is False, name


@pytest.mark.asyncio
async def test_a_search_gives_a_handful_of_hits_with_licence_and_file_and_never_the_svg(
    project,
):
    result = await call("search_icons", {"query": "bank", "limit": 5})

    found = result.structured_content
    assert found["count"] == len(found["icons"]) == 5
    for icon in found["icons"]:
        assert set(icon) == {"id", "name", "set", "tags", "licence", "path"}
        assert icon["id"] == f"{icon['set']}/{icon['name']}"
        assert icon["licence"] in {"CC0-1.0", "MIT", "Apache-2.0"}
        assert len(icon["tags"]) <= 8
    assert "<svg" not in text(result) and len(text(result)) < 3000
    assert "save_vector" in found["next"]


@pytest.mark.asyncio
async def test_a_search_comes_with_an_image_of_its_hits_numbered_in_the_order_listed(project, sheet):
    result = await call("search_icons", {"query": "bank", "limit": 4})

    found = result.structured_content
    (picture,) = images(result)
    assert picture.format == "JPEG" and picture.width <= 1000
    assert sheet == [[icon["id"] for icon in found["icons"]]]
    assert "look at the image" in found["next"] and '"count": 4' in text(result)


@pytest.mark.asyncio
async def test_a_search_still_answers_when_the_icons_cannot_be_drawn(project, monkeypatch):
    def no_browser(icons, folder, **_):
        raise icon_sheet.IconSheetError("the icon sheet needs the resvg-py package")

    monkeypatch.setattr(icon_sheet, "icon_sheet", no_browser)

    result = await call("search_icons", {"query": "bank", "limit": 3})

    assert not result.is_error and images(result) == []
    found = result.structured_content
    assert found["count"] == 3 and "no image of the icons" in found["next"]
    assert "save_vector" in found["next"]


@pytest.mark.asyncio
async def test_a_search_with_no_hit_draws_nothing(project, sheet):
    result = await call("search_icons", {"query": "zzzz nothing"})

    assert images(result) == [] and sheet == []


@pytest.mark.asyncio
async def test_a_search_with_no_hit_says_to_try_other_words(project):
    found = (await call("search_icons", {"query": "zzzz nothing"})).structured_content

    assert found["count"] == 0 and found["icons"] == [] and "other words" in found["next"]


@pytest.mark.asyncio
async def test_the_default_is_eight_hits(project):
    assert (await call("search_icons", {"query": "arrow"})).structured_content["count"] == 8


@pytest.mark.asyncio
async def test_a_vector_written_by_the_chat_is_saved_and_the_answer_says_how_to_place_it(
    project,
):
    result = await call("save_vector", {"svg": ARROW, "name": "arrow"})

    done = result.structured_content
    assert not result.is_error
    assert done["path"] == "assets/media/vectors/arrow.svg"
    assert (done["source"], done["licence"]) == (
        "claude-written",
        "LicenseRef-claude-written",
    )
    assert (project / done["path"]).read_text(encoding="utf-8").strip() == ARROW
    assert "add_graphic(vector='arrow'" in done["next"]


@pytest.mark.asyncio
async def test_an_icon_id_from_the_search_is_saved_with_its_licence(project):
    hit = (await call("search_icons", {"query": "anchor", "limit": 1})).structured_content["icons"][0]

    done = (await call("save_vector", {"svg": hit["id"], "name": "anchor"})).structured_content

    assert done["source"] == hit["set"] and done["licence"] == hit["licence"]
    record = json.loads((project / f"{done['path']}.json").read_text(encoding="utf-8"))
    assert record["icon"] == hit["id"] and record["notice"]


@pytest.mark.asyncio
async def test_a_refused_vector_comes_back_in_words_every_problem_at_once_and_nothing_is_written(
    project,
):
    bad = ARROW.replace("<path", '<script>x()</script><path fill="red" onclick="x()"')

    refused = await call("save_vector", {"svg": bad, "name": "bad"})

    assert refused.is_error
    message = text(refused)
    assert "script" in message and 'fill="red"' in message and "onclick" in message
    assert not (project / "assets/media/vectors").exists()


@pytest.mark.asyncio
async def test_a_bad_name_an_unknown_icon_and_a_taken_name_are_refused_in_words(
    project,
):
    await call("save_vector", {"svg": ARROW, "name": "arrow"})

    assert "letters, digits" in text(await call("save_vector", {"svg": ARROW, "name": "../x"}))
    assert "search_icons" in text(await call("save_vector", {"svg": "tabler/none", "name": "x"}))
    assert "never overwritten" in text(await call("save_vector", {"svg": ARROW, "name": "arrow"}))


@pytest.fixture
def tracer(monkeypatch):
    class Fake:
        def convert_raw_image_to_svg(self, data, img_format, colormode, *rest):
            return (
                '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="640" height="320">'
                '<path d="M0 0h10v10z" fill="#000000" transform="translate(5,5)"/></svg>'
            )

    monkeypatch.setitem(sys.modules, "vtracer", Fake())


@pytest.mark.asyncio
async def test_a_picture_of_the_project_is_traced_into_a_vector(project, tracer):
    Image.new("RGB", (640, 320), "white").save(project / "seal.png")

    result = await call("vectorize", {"image": "seal.png", "name": "seal"})

    done = result.structured_content
    assert not result.is_error and done["source"] == "vectorized"
    assert 'viewBox="0 0 640 320"' in (project / done["path"]).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_without_vtracer_the_tool_says_how_to_install_it_and_a_picture_must_be_in_the_project(
    project, monkeypatch, tracer, tmp_path
):
    Image.new("RGB", (64, 64), "white").save(tmp_path / "outside.png")

    outside = await call("vectorize", {"image": str(tmp_path / "outside.png"), "name": "x"})
    monkeypatch.setitem(sys.modules, "vtracer", None)
    Image.new("RGB", (64, 64), "white").save(project / "seal.png")
    missing = await call("vectorize", {"image": "seal.png", "name": "y"})

    assert outside.is_error and "no picture" in text(outside)
    assert missing.is_error and "pip install 'vtracer>=" in text(missing)


@pytest.mark.asyncio
async def test_a_saved_vector_is_added_as_a_layer_the_layer_table_reads_and_the_checks_pass(project, monkeypatch):
    clean = {"ok": True, "errorCount": 0, "warningCount": 0, "findings": []}
    monkeypatch.setattr(hf_cli, "lint", lambda project, node_dir=None: clean)
    await call("save_vector", {"svg": ARROW, "name": "arrow"})

    result = await call(
        "add_graphic",
        {
            "vector": "arrow",
            "start": 2,
            "duration": 4,
            "position": "top-right",
            "size": "large",
            "color": "oxblood",
        },
    )

    added = result.structured_content
    assert not result.is_error, text(result)
    assert (added["layer"], added["shot"], added["start"], added["end"]) == (
        "l-p1-3",
        "p1",
        2.0,
        6.0,
    )
    assert added["vector"] == "assets/media/vectors/arrow.svg" and added["z_index"] > 2
    assert len(text(result)) < 2000
    table = (await call("layer_table", {"kinds": ["graphic-stamp"]})).structured_content["layers"]
    assert [(row["id"], row["kind"], row["source"]) for row in table] == [("l-p1-3", "graphic-stamp", "graphic")]
    checked = (await call("run_checks")).structured_content
    assert checked["ok"] is True and checked["findings"] == []
    # block_info knows the block, for the next call
    info = (await call("block_info", {"component": "graphic-stamp"})).structured_content
    assert [v["id"] for v in info["variables"]][:2] == ["durationSeconds", "svg"]


@pytest.mark.asyncio
async def test_a_wrong_vector_option_or_time_is_refused_in_words_and_the_page_is_not_touched(
    project,
):
    await call("save_vector", {"svg": ARROW, "name": "arrow"})
    before = (project / "index.html").read_text(encoding="utf-8")

    cases = {
        "no vector": ({"vector": "ghost", "start": 1, "duration": 3}, "save_vector"),
        "position": (
            {"vector": "arrow", "start": 1, "duration": 3, "position": "middle"},
            "top-left",
        ),
        "short": ({"vector": "arrow", "start": 1, "duration": 1}, "at least 2.4 s"),
        "late": ({"vector": "arrow", "start": 25, "duration": 3}, "past the end"),
    }
    for name, (arguments, words) in cases.items():
        refused = await call("add_graphic", arguments)

        assert refused.is_error and words in text(refused), (name, text(refused))
    assert (project / "index.html").read_text(encoding="utf-8") == before
