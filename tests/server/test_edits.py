"""The shared history wrapper of the edit tools (``hyperframes_mcp/server/edits.py``): a turn of ours around a change to the page,
through the app or on disk, against the fake app and the fake CLI."""

from __future__ import annotations

import pytest

from hyperframes_mcp.motion.project_edit import EditError
from hyperframes_mcp.server import edits
from tests.server.fake_app import FakeApp

pytestmark = pytest.mark.asyncio


def tools(app) -> list[str]:
    return [c.tool for c in app.calls if c.tool != "project.list"]


async def test_a_change_made_through_the_app_is_one_entry_of_ours(bridge, app, app_folder):
    async with edits.turn(bridge, app_folder, "Set a thing") as turn:
        result = await turn.edit(lambda page: (page.replace("v1", "v2"), "done"))

    assert result == "done" and turn.route == "app"
    assert (app_folder / "index.html").read_text() == "<html>v2</html>"
    assert turn.entry["who"] == {"kind": "agent", "name": "hyperframes-mcp"}
    assert turn.entry["label"] == "Set a thing" and turn.entry["files"] == ["index.html"]
    assert tools(app) == ["history.begin", "file.read", "file.write", "history.end"]


async def test_a_page_the_cli_wrote_on_disk_inside_the_turn_is_in_the_apps_entry_too(bridge, app, app_folder):
    # the app files every change to the project's files between begin and end, whichever process wrote them: read from
    # its code (the window's close sweeps the folder), modelled in the fake
    async with edits.turn(bridge, app_folder, "Timeline: trim") as turn:
        (app_folder / "index.html").write_text("<html>written by the CLI</html>")

    assert turn.entry["files"] == ["index.html"] and turn.entry["who"]["name"] == "hyperframes-mcp"
    assert tools(app) == ["history.begin", "history.end"]


async def test_an_edit_that_changes_nothing_writes_nothing_and_leaves_no_entry(bridge, app, app_folder):
    async with edits.turn(bridge, app_folder, "Set a thing") as turn:
        await turn.edit(lambda page: (page, None))

    assert turn.entry is None and "file.write" not in tools(app)


async def test_a_turn_ends_when_its_block_fails_and_what_was_written_stays_an_entry(bridge, app, app_folder):
    with pytest.raises(RuntimeError):
        async with edits.turn(bridge, app_folder, "Set a thing") as turn:
            await turn.edit(lambda page: (page + "!", None))
            raise RuntimeError("the second step broke")

    assert tools(app)[-1] == "history.end"
    assert [e["label"] for e in app.history["demo"]] == ["Set a thing"]


async def test_a_page_that_keeps_changing_is_read_twice_and_then_an_error(bridge, app, app_folder):
    counter = iter(range(10))
    app.after_read = lambda project_id, path: app.person_edit(project_id, path, f"<html>{next(counter)}</html>")

    with pytest.raises(EditError, match="changed twice while it was being written"):
        async with edits.turn(bridge, app_folder, "Set a thing") as turn:
            await turn.edit(lambda page: (page + "!", None))

    assert tools(app).count("file.read") == 2 and tools(app)[-1] == "history.end"


async def test_without_the_app_the_page_is_written_on_disk_inside_the_clis_history(
    bridge, app, app_folder, hyperframes
):
    app.quit()

    async with edits.turn(bridge, app_folder, "Set a thing") as turn:
        await turn.edit(lambda page: (page.replace("v1", "v2"), None))

    assert turn.route == "disk" and (app_folder / "index.html").read_text() == "<html>v2</html>"
    assert turn.entry["label"] == "Set a thing" and turn.entry["who"]["name"] == "hyperframes-mcp"
    assert [c[:2] for c in hyperframes.calls] == [
        ["history", "begin"],
        ["history", "end"],
    ]
    assert app.calls == []


async def test_an_app_that_does_not_list_the_project_is_the_disk_route_too(tmp_path, app_folder, hyperframes):
    from hyperframes_mcp.server.bridge import AppBridge

    other = FakeApp(tmp_path / "support-2", {"elsewhere": tmp_path})

    async with edits.turn(AppBridge(other.support, transport=other.transport), app_folder, "x") as turn:
        await turn.edit(lambda page: (page + " ", None))

    assert turn.route == "disk"
    # the probe and the listing of Home's projects: no history was opened in the app
    assert {c.tool for c in other.calls} == {"project.list"}
