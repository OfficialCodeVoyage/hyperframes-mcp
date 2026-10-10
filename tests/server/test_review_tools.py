"""The tools that check and show a project as the HyperFrames app's agent calls them, through an in-memory MCP client:
the checks the app's turn check misses, a frame and a contact sheet as images, and what a block is for. The CLI's lint
is faked (``tests/motion`` has its real shapes); a frame comes from the fake app's preview, and no headless browser is
ever run (a fake that fails on one stands in for it).
"""

from __future__ import annotations

import base64
import re
from io import BytesIO

import pytest
from mcp import Client
from PIL import Image

from hyperframes_mcp.motion import hf_cli, manifest
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.server import build_server
from tests.motion.test_project_edit import composed, two_shots
from tests.server import fake_app
from tests.server.fake_app import FakeApp, ToolRefused
from tests.server.test_tools import call, text

CLEAN = {"ok": True, "errorCount": 0, "warningCount": 0, "findings": []}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project 20 s long (credit-line layers l-p1-2 and l-m1-2 over a photo and a clip), the session's folder."""
    root = composed(tmp_path, two_shots())[0]
    manifest.write_manifest(root, manifest.ProjectManifest(profile="history"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def cli(monkeypatch):
    """A fake CLI: the lint finds nothing; the browser check and the snapshot (a headless Chrome) fail the test if they
    run. ``calls`` records what ran."""
    calls: list[tuple] = []

    def lint(project, node_dir=None):
        calls.append(("lint",))
        return CLEAN

    def no_browser(*args, **kwargs):
        raise AssertionError("no headless browser may run")

    monkeypatch.setattr(hf_cli, "lint", lint)
    monkeypatch.setattr(hf_cli, "check", no_browser)
    monkeypatch.setattr(hf_cli, "snapshot", no_browser)
    return calls


def images(result) -> list[Image.Image]:
    return [Image.open(BytesIO(base64.b64decode(part.data))) for part in result.content if part.type == "image"]


@pytest.mark.asyncio
async def test_the_review_tools_only_read(project):
    async with Client(build_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in ("run_checks", "get_frame", "contact_sheet", "block_info"):
        assert tools[name].annotations.read_only_hint is True, name


@pytest.mark.asyncio
async def test_run_checks_gives_a_clean_project_one_ok_line(project, cli):
    result = await call("run_checks")

    checked = result.structured_content
    assert checked["ok"] is True and checked["findings"] == []
    assert checked["summary"].startswith("ok: no problems in 4 layers")
    assert cli == [("lint",)]


@pytest.mark.asyncio
async def test_run_checks_names_a_credit_hidden_by_its_picture_and_runs_no_browser(project, cli):
    page = project / "index.html"
    page.write_text(
        re.sub(
            r'(id="l-p1-2".*?style="z-index: )\d+',
            r"\g<1>0",
            page.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )

    result = await call("run_checks")

    checked = result.structured_content
    assert checked["ok"] is False
    first = checked["findings"][0]
    assert (first["severity"], first["layer"], first["time"]) == (
        "error",
        "l-p1-1",
        0.0,
    )
    assert "credit line" in first["what"] and "higher track" in first["what"]
    assert cli == [("lint",)]


def png_of_a_frame() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (960, 540), (120, 90, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def frame_app(tmp_path, project, monkeypatch):
    """The fake app open on the project, answering ``preview.frame`` with a real picture."""
    monkeypatch.setattr(fake_app, "PNG", png_of_a_frame())
    return FakeApp(tmp_path / "support", {"demo": project})


def via(app) -> AppBridge:
    return AppBridge(app.support, transport=app.transport)


@pytest.mark.asyncio
async def test_get_frame_comes_from_the_apps_preview(project, cli, frame_app):
    result = await call("get_frame", {"time": 4.5}, via(frame_app))

    (frame,) = images(result)
    assert frame.format == "JPEG" and frame.width <= 960
    assert [part.mime_type for part in result.content if part.type == "image"] == ["image/jpeg"]
    assert "4.5 s, from the app" in text(result)
    assert cli == []
    (asked,) = [c for c in frame_app.calls if c.tool == "preview.frame"]
    assert asked.arguments == {"projectId": "demo", "time": 4.5}
    assert not [c for c in frame_app.calls if c.tool.startswith(("history", "file"))]  # an image of a moment


@pytest.mark.asyncio
async def test_get_frame_with_the_app_closed_or_not_showing_the_project_says_to_open_it(
    project, cli, frame_app, tmp_path
):
    frame_app.quit()
    closed = await call("get_frame", {"time": 2}, via(frame_app))
    elsewhere = FakeApp(tmp_path / "support-2", {"other": tmp_path})
    unlisted = await call("get_frame", {"time": 3}, via(elsewhere))

    for result in (closed, unlisted):
        assert result.is_error and images(result) == []
        assert "does not have this project open" in text(result)
    assert cli == []  # no headless browser stands in for the app


@pytest.mark.asyncio
async def test_get_frame_right_after_the_app_reloaded_a_page_still_gets_the_frame(project, cli, frame_app):
    # the app is busy for the first second after a page we wrote (the one-second probe gets no answer), then answers
    frame_app.busy_for = 1

    result = await call("get_frame", {"time": 4.5}, via(frame_app))

    assert not result.is_error and len(images(result)) == 1
    assert cli == []


@pytest.mark.asyncio
async def test_get_frame_says_why_the_app_gave_none(project, cli, frame_app):
    def no_window(arguments, who):
        raise ToolRefused("the project is not open in a window")

    frame_app.tool_preview_frame = no_window

    result = await call("get_frame", {"time": 4.5}, via(frame_app))

    assert result.is_error
    assert "the app gave no frame at 4.5 s: the project is not open in a window" in text(result)
    assert cli == []


@pytest.mark.asyncio
async def test_a_time_outside_the_video_is_refused_before_the_app_is_asked(project, cli, frame_app):
    result = await call("get_frame", {"time": 99}, via(frame_app))

    assert result.is_error and "20 s long" in text(result)
    assert frame_app.calls == [] and cli == []


@pytest.mark.asyncio
async def test_contact_sheet_returns_one_labelled_sheet_of_the_apps_frames_in_order(project, cli, frame_app):
    result = await call("contact_sheet", {"from_s": 2, "to_s": 13}, via(frame_app))

    (sheet,) = images(result)
    assert sheet.format == "JPEG" and sheet.width <= 1000
    asked = [c.arguments["time"] for c in frame_app.calls if c.tool == "preview.frame"]
    assert asked == pytest.approx([2 + n for n in range(12)])
    assert "12 frames" in text(result) and "from the app" in text(result)
    assert cli == []


@pytest.mark.asyncio
async def test_a_contact_sheet_with_the_app_closed_says_to_open_it_and_asks_nothing(project, cli, frame_app):
    frame_app.quit()

    result = await call("contact_sheet", {"from_s": 2, "to_s": 13}, via(frame_app))

    assert result.is_error and "does not have this project open" in text(result)
    assert cli == []


@pytest.mark.asyncio
async def test_a_contact_sheet_over_the_limit_is_refused_before_the_app_is_asked(project, cli, frame_app):
    result = await call("contact_sheet", {"from_s": 0, "to_s": 10, "n": 17}, via(frame_app))

    assert result.is_error and "at most 16" in text(result)
    assert frame_app.calls == []


@pytest.mark.asyncio
async def test_block_info_lists_every_block_small_and_gives_one_in_full():
    listed = (await call("block_info")).structured_content
    stamp = (await call("block_info", {"component": "date-stamp"})).structured_content

    assert "date-stamp" in [b["name"] for b in listed["blocks"]]
    assert stamp["name"] == "date-stamp"
    assert "top-right" in {v["id"]: v for v in stamp["variables"]}["position"]["options"]


@pytest.mark.asyncio
async def test_block_info_of_a_layer_gives_its_block_with_what_each_variable_is_set_to(
    project,
):
    info = (await call("block_info", {"layer": "l-p1-2"})).structured_content

    assert info["name"] == "credit-line"
    assert info["layer"]["id"] == "l-p1-2"
    now = {v["id"]: v["now"] for v in info["variables"]}
    assert now["text"] == "US Army Signal Corps, NARA 111-SC-204515"
    assert now["align"] == "left" or now["align"] is None


@pytest.mark.asyncio
async def test_block_info_of_a_layer_refuses_an_unknown_layer_and_a_block_that_is_not_its_own(
    project,
):
    missing = await call("block_info", {"layer": "l-p9-1"})
    wrong = await call("block_info", {"layer": "l-p1-2", "component": "counter"})

    assert missing.is_error and "no layer 'l-p9-1'" in text(missing)
    assert wrong.is_error and "l-p1-2 is a credit-line, not a counter" in text(wrong)


@pytest.mark.asyncio
async def test_block_info_of_an_unknown_block_lists_the_known_ones():
    result = await call("block_info", {"component": "date-plate"})

    assert result.is_error and "photo-shot" in text(result)
