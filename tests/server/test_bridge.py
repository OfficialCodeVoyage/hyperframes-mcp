"""The bridge to the HyperFrames app, against the fake app (``fake_app.py``): finding it, using it, losing it, and never
showing its token."""

from __future__ import annotations

import json
import logging

import pytest

from hyperframes_mcp.server.bridge import (
    AppBridge,
    AppBusy,
    AppConflict,
    AppError,
    AppProjectUnknown,
    AppUnavailable,
)
from tests.server.fake_app import PNG, TOKEN, FakeApp, ToolRefused

pytestmark = pytest.mark.asyncio


# ----- finding the app


async def test_the_app_is_found_from_its_port_and_token_files_and_asked_as_hyperframes_mcp(bridge, app):
    assert await bridge.available() is True

    call = app.calls[0]
    assert call.tool == "project.list"
    # the first word of the User-Agent is the author the app files our history entries under
    assert call.user_agent == "hyperframes-mcp/1"
    assert call.user_agent.split("/")[0].lower() not in {"claude", "codex"}


async def test_an_app_that_never_ran_is_not_available_and_not_a_crash(tmp_path):
    bridge = AppBridge(tmp_path / "no-such-folder")

    assert await bridge.available() is False
    with pytest.raises(AppUnavailable, match="not running"):
        await bridge.project_id(tmp_path)


async def test_a_stale_port_file_means_not_available(tmp_path, app):
    # the file names a port nothing listens on: the app was quit, or it took another port
    (app.support / "studio-port.json").write_text(json.dumps({"port": 54999}))
    bridge = AppBridge(app.support, transport=app.transport)

    assert await bridge.available() is False
    with pytest.raises(AppUnavailable):
        await bridge.history_list("demo")


@pytest.mark.parametrize("contents", ["not json", "[]", '{"port": "54321"}', '{"port": 80}', "{}"])
async def test_a_damaged_port_file_means_not_available(app, bridge, contents):
    (app.support / "studio-port.json").write_text(contents)

    assert await bridge.available() is False


async def test_a_quit_app_is_lost_and_a_reopened_one_found_again_without_a_new_bridge(bridge, app):
    assert await bridge.available() is True

    app.quit()
    assert await bridge.available() is False

    # reopened on another port, with a renewed token, the files rewritten
    app.running = True
    app.port, app.token = 54777, "another-fake-token-9876"
    app.write_discovery_files()
    assert await bridge.available() is True


async def test_a_renewed_token_that_is_not_in_the_file_yet_is_refused_as_unavailable(bridge, app):
    app.token = "renewed-fake-token"  # the app moved on; mcp.json still holds the old one

    assert await bridge.available() is False
    with pytest.raises(AppUnavailable, match="refused the token"):
        await bridge.projects()


async def test_a_signed_out_app_is_unavailable_in_its_own_words(bridge, app):
    app.signed_in = False

    assert await bridge.available() is False
    with pytest.raises(AppUnavailable, match="Sign in to HyperFrames"):
        await bridge.projects()


async def test_a_signed_out_app_refuses_the_selection_the_same_way(bridge, app):
    app.signed_in = False

    with pytest.raises(AppUnavailable, match="Sign in to HyperFrames"):
        await bridge.selection("demo")


async def test_an_app_that_does_not_answer_in_time_is_unavailable(bridge, app):
    app.hang = True

    assert await bridge.available() is False
    with pytest.raises(AppUnavailable):
        await bridge.history_list("demo")


async def test_a_busy_app_is_waited_for_once_and_is_not_taken_for_a_closed_one(bridge, app):
    # right after it reloads a page we wrote, the app misses the one-second probe and then answers
    app.busy_for = 1
    assert await bridge.available() is True

    app.busy_for = 2  # still busy at the second try: no answer, and the words say it is busy, not closed
    assert await bridge.available() is False
    app.busy_for = 1
    with pytest.raises(AppBusy, match="busy"):
        await bridge.history_list("demo")


async def test_a_closed_app_is_not_waited_for(bridge, app):
    app.quit()

    assert await bridge.available() is False
    assert app.calls == []  # it refused the connection: one try, no second
    with pytest.raises(AppUnavailable, match="not running") as refused:
        await bridge.history_list("demo")
    assert not isinstance(refused.value, AppBusy)


# ----- the token never shows

LEAKS = ("fake-app-token", TOKEN)


async def test_the_token_is_in_no_log_no_error_and_no_result(bridge, app, app_folder, caplog):
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []

    async def note(awaitable):
        try:
            seen.append(repr(await awaitable))
        except AppError as error:
            seen.append(str(error))
            seen.append(repr(error))

    await note(bridge.available())
    await note(bridge.projects())
    await note(bridge.project_id(app_folder))
    await note(bridge.comments_post("demo", "the credit is late", 12.5))
    await note(bridge.comments_list("demo"))
    await note(bridge.file_read("demo", "index.html"))
    await note(bridge.file_write("demo", "index.html", "x", '"stale"'))
    await note(bridge.history_list("demo", since="not-a-time"))
    await note(bridge.call("no.such"))
    # an app that echoes its token in a refusal: it is taken out

    def refuse(arguments, who):
        raise ToolRefused(f"failed at http://127.0.0.1:1/?token={app.token}")

    app.tool_history_end = refuse
    await note(bridge.history_end("demo"))

    everything = caplog.text + "\n".join(seen)
    assert "<token>" in everything  # the refusal above was scrubbed, not dropped
    for leak in LEAKS:
        assert leak not in everything


async def test_the_apps_link_with_its_token_is_dropped_from_a_posted_comment(bridge):
    posted = await bridge.comments_post("demo", "title card is 2 frames late", 31.0)

    assert "browserUrl" not in posted
    assert TOKEN not in json.dumps(posted)
    assert posted["number"] == 1 and posted["status"] == "waiting"


# ----- projects


async def test_a_project_is_found_by_its_folder_through_the_link_home_records(tmp_path, app_folder):
    link = tmp_path / "home" / "demo"
    link.parent.mkdir()
    link.symlink_to(app_folder)  # Home keeps an alias, the app lists the alias
    app = FakeApp(tmp_path / "support", {"demo": link})
    bridge = AppBridge(app.support, transport=app.transport)

    assert await bridge.project_id(app_folder) == "demo"
    assert await bridge.project_id(link) == "demo"


async def test_a_folder_home_does_not_list_is_named_so(bridge, tmp_path):
    with pytest.raises(AppProjectUnknown, match="open this project in the app"):
        await bridge.project_id(tmp_path / "elsewhere")


async def test_every_page_of_the_project_list_is_read(tmp_path):
    projects = {}
    for n in range(230):
        folder = tmp_path / "many" / f"p{n:03}"
        folder.mkdir(parents=True)
        projects[f"p{n:03}"] = folder
    app = FakeApp(tmp_path / "support", projects)
    bridge = AppBridge(app.support, transport=app.transport)

    assert len(await bridge.projects()) == 230
    assert await bridge.project_id(projects["p229"]) == "p229"


# ----- files


async def test_a_file_is_read_with_its_version_and_written_against_it(bridge, app_folder):
    page = await bridge.file_read("demo", "index.html")
    assert page.content == "<html>v1</html>" and page.version.startswith('"sha256:')

    version = await bridge.file_write("demo", "index.html", "<html>v2</html>", page.version)

    assert (app_folder / "index.html").read_text() == "<html>v2</html>"
    assert version != page.version
    assert (await bridge.file_read("demo", "index.html")).version == version


async def test_a_stale_version_is_a_conflict_the_caller_can_read_again_and_retry(bridge, app, app_folder):
    page = await bridge.file_read("demo", "index.html")
    app.person_edit("demo", "index.html", "<html>the person's edit</html>")

    with pytest.raises(AppConflict, match="changed since your read"):
        await bridge.file_write("demo", "index.html", "<html>mine</html>", page.version)
    assert (app_folder / "index.html").read_text() == "<html>the person's edit</html>"

    # the caller's retry: read again, redo the change on the new content, write with the new version
    again = await bridge.file_read("demo", "index.html")
    await bridge.file_write(
        "demo",
        "index.html",
        again.content.replace("edit", "edit + mine"),
        again.version,
    )
    assert "edit + mine" in (app_folder / "index.html").read_text()


async def test_a_new_file_is_written_with_no_version_and_only_once(bridge, app_folder):
    await bridge.file_write("demo", "notes/qa.md", "first", None)
    assert (app_folder / "notes" / "qa.md").read_text() == "first"

    with pytest.raises(AppConflict):
        await bridge.file_write("demo", "notes/qa.md", "second", None)


async def test_a_missing_file_or_project_is_the_apps_refusal_in_words(bridge):
    with pytest.raises(AppError, match="No file"):
        await bridge.file_read("demo", "nope.html")
    with pytest.raises(AppError, match="No project with id"):
        await bridge.file_read("no-such", "index.html")


# ----- history


async def test_a_turn_is_one_entry_of_hyperframes_mcp_that_undoes_alone(bridge, app, app_folder):
    app.person_edit("demo", "index.html", "<html>person</html>")
    page = await bridge.file_read("demo", "index.html")

    await bridge.history_begin("demo", "trim l-vault-1")
    await bridge.file_write("demo", "index.html", "<html>ours 1</html>", page.version)
    page = await bridge.file_read("demo", "index.html")
    await bridge.file_write("demo", "index.html", "<html>ours 2</html>", page.version)
    entry = await bridge.history_end("demo")

    assert entry["who"] == {"kind": "agent", "name": "hyperframes-mcp"}
    assert entry["label"] == "trim l-vault-1" and entry["files"] == ["index.html"]
    listed = await bridge.history_list("demo", since="entry-1")
    assert [e["label"] for e in listed] == ["trim l-vault-1"]

    undone = await bridge.history_undo("demo")

    assert undone["ok"] is True
    assert (app_folder / "index.html").read_text() == "<html>person</html>"
    everything = await bridge.history_list("demo", since="2000-01-01T00:00:00Z")
    assert [e["undone"] for e in everything] == [
        False,
        True,
        False,
    ]  # the undo, ours, the person's


async def test_an_undo_is_refused_when_a_file_changed_since_and_the_conflict_is_reported(bridge, app, app_folder):
    page = await bridge.file_read("demo", "index.html")
    await bridge.file_write("demo", "index.html", "<html>ours</html>", page.version)
    app.person_edit("demo", "index.html", "<html>the person went on</html>")

    undone = await bridge.history_undo("demo")

    assert undone["ok"] is False and undone["conflict"]["files"] == ["index.html"]
    assert (app_folder / "index.html").read_text() == "<html>the person went on</html>"


async def test_a_change_of_someone_else_is_not_ours_to_undo(bridge, app):
    app.person_edit("demo", "index.html", "<html>person</html>")

    with pytest.raises(AppError, match="no change to undo"):
        await bridge.history_undo("demo")
    with pytest.raises(AppError, match="not yours"):
        await bridge.history_undo("demo", entry_id="entry-1")


async def test_history_since_mine_is_what_came_after_our_last_entry(bridge, app):
    page = await bridge.file_read("demo", "index.html")
    await bridge.file_write("demo", "index.html", "<html>ours</html>", page.version)
    app.person_edit("demo", "index.html", "<html>person</html>")

    after = await bridge.history_list("demo")

    assert [e["who"]["name"] for e in after] == ["You", "hyperframes-mcp"]
    with pytest.raises(AppError, match="entry id, an ISO time"):
        await bridge.history_list("demo", since="yesterday-ish")


# ----- the picture, the picks and the comments


async def test_a_frame_comes_back_as_png_bytes(bridge, app):
    assert await bridge.preview_frame("demo", 61.5) == PNG
    assert app.calls[-1].arguments == {"projectId": "demo", "time": 61.5}
    with pytest.raises(AppError, match="time"):
        await bridge.preview_frame("demo", -1)


async def test_the_selection_is_what_the_person_picked_or_none(bridge, app):
    assert await bridge.selection("demo") is None

    app.select("demo", currentTime=12.5, target={"id": "l-vault-1"}, label="vault")
    picked = await bridge.selection("demo")

    assert picked["currentTime"] == 12.5 and picked["target"]["id"] == "l-vault-1"
    with pytest.raises(AppProjectUnknown):
        await bridge.selection("no-such")


async def test_comments_are_posted_at_a_time_and_listed_by_status(bridge):
    await bridge.comments_post("demo", "credit covers the face", 61.0)
    await bridge.comments_post("demo", "dissolve is late", 90.25)

    threads = await bridge.comments_list("demo")

    assert [(t["anchor"]["time"], t["body"]) for t in threads] == [
        (61.0, "credit covers the face"),
        (90.25, "dissolve is late"),
    ]
    assert await bridge.comments_list("demo", "resolved") == []
    with pytest.raises(AppError, match="Invalid comment"):
        await bridge.comments_post("demo", "", 5.0)
