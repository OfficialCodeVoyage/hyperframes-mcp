"""The tools that go through the HyperFrames app, as the app's agent calls them (an in-memory MCP client), with the bridge
pointed at the fake app by injection: what the person picked, what changed, comments on the timeline, and what each
does when the app is not running."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest
from mcp import Client

from hyperframes_mcp.motion import manifest
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.server import build_server
from tests.motion.test_project_edit import composed, two_shots
from tests.server.fake_app import TOKEN, FakeApp
from tests.server.test_selection import PICK_AT_HALF_A_SECOND, REAL_PLAYHEAD

pytestmark = pytest.mark.asyncio


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project of two shots (the photo 0 to 8 s, the clip 8 to 16 s) with narration, as the session's folder."""
    root = composed(tmp_path, two_shots())[0]
    words = tmp_path / "words.json"
    words.write_text(
        json.dumps(
            [
                {"word": "It", "start": 0.2, "end": 0.4},
                {"word": "is", "start": 0.4, "end": 0.6},
                {"word": "April.", "start": 0.6, "end": 1.0},
            ]
        )
    )
    manifest.write_manifest(
        root,
        manifest.ProjectManifest(profile="history", topic_id=1, alignment=words),
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def open_app(tmp_path, project):
    """The fake app, open on the project as ``demo``."""
    return FakeApp(tmp_path / "support", {"demo": project})


async def call(app: FakeApp, name: str, arguments: dict | None = None):
    """One tool call, with the server's bridge pointed at ``app``."""
    bridge = AppBridge(app.support, transport=app.transport)
    async with Client(build_server(bridge)) as client:
        return await client.call_tool(name, arguments or {})


def text(result) -> str:
    return "".join(getattr(part, "text", "") for part in result.content)


# ----- resolve_selection


async def test_a_messages_app_context_resolves_to_the_layers_sentence_and_credits(
    open_app,
):
    result = await call(open_app, "resolve_selection", {"app_context": PICK_AT_HALF_A_SECOND})

    found = result.structured_content
    assert found["source"] == "app_context" and found["at"] == 0.5
    assert [layer["id"] for layer in found["layers"]] == ["l-p1-1"]
    assert found["layers"][0]["picked"] is True
    assert found["narration"] == "It is April." and found["speaking"] is True
    assert [c["id"] for c in found["credits"]] == ["l-p1-2"]
    assert open_app.calls == []  # a block needs no app


async def test_a_playhead_block_resolves_without_the_app_running(open_app):
    open_app.quit()

    result = await call(open_app, "resolve_selection", {"app_context": REAL_PLAYHEAD})

    assert not result.is_error
    assert result.structured_content["at"] == 61.62
    assert result.structured_content["layers"] == []  # nothing is on screen at 1:01 in this short project


async def test_use_app_asks_the_app_what_is_picked_now(open_app):
    open_app.select("demo", currentTime=9.0, target={"id": "l-m1-1", "selector": "#l-m1-1"})

    result = await call(open_app, "resolve_selection", {"use_app": True})

    found = result.structured_content
    assert found["source"] == "app" and found["shots"] == ["m1"]
    assert found["picked"] == [{"selector": "#l-m1-1", "layer": "l-m1-1"}]


async def test_use_app_with_nothing_picked_says_so(open_app):
    result = await call(open_app, "resolve_selection", {"use_app": True})

    assert result.structured_content["selected"] is False
    assert "click an element" in result.structured_content["note"]


async def test_use_app_with_the_app_closed_points_to_the_app_context(open_app):
    open_app.quit()

    result = await call(open_app, "resolve_selection", {"use_app": True})

    assert result.is_error
    assert "not running" in text(result) and "app_context" in text(result)


async def test_with_neither_an_app_context_nor_use_app_it_says_what_to_pass(open_app):
    result = await call(open_app, "resolve_selection")

    assert result.is_error and "app_context" in text(result)


# ----- catch_up


async def test_catch_up_with_the_app_lists_what_changed_and_who_changed_it(open_app, project):
    open_app.person_edit("demo", "index.html", (project / "index.html").read_text() + " ")
    page = await AppBridge(open_app.support, transport=open_app.transport).file_read("demo", "index.html")
    await AppBridge(open_app.support, transport=open_app.transport).file_write(
        "demo", "index.html", page.content + "<!-- ours -->", page.version
    )
    open_app.person_edit("demo", "VIDEO.md", "a note\n")

    result = await call(open_app, "catch_up")

    found = result.structured_content
    assert found["route"] == "app" and found["since"] == "mine"
    # since "mine" is from our own last entry on: ours, then the person's note
    assert [(e["who"], e["files"]) for e in found["history"]] == [
        ("You", ["VIDEO.md"]),
        ("hyperframes-mcp", ["index.html"]),
    ]
    assert found["history"][0]["kind"] == "person"


async def test_a_long_label_and_many_files_are_cut(open_app, project):
    entry = open_app.history.setdefault("demo", [])
    open_app.person_edit("demo", "a.txt", "x")
    entry[-1]["label"] = "Please " + "do this thing " * 40
    for n in range(9):
        entry[-1]["files"][f"f{n}.txt"] = [None, "x"]

    found = (await call(open_app, "catch_up")).structured_content

    shown = found["history"][0]
    assert len(shown["label"]) <= 100 and shown["label"].endswith("…")
    assert len(shown["files"]) == 6 and shown["files_more"] == 4


async def test_catch_up_also_lists_the_app_agents_own_runs_from_its_log(open_app, project):
    log = project / ".hyperframes" / "agent-runs.jsonl"
    log.parent.mkdir()
    old = {
        "id": "r1",
        "number": 1,
        "kind": "run",
        "status": "done",
        "startedAt": 1_791_000_000_000,
        "endedAt": 1_791_000_030_000,
        "instruction": "first",
    }
    new = {
        "id": "r2",
        "number": 2,
        "kind": "run",
        "status": "done",
        "startedAt": 1_791_100_000_000,
        "endedAt": 1_791_100_060_000,
        "instruction": "Review the first 60 seconds " + "x" * 300,
        "changedFiles": ["index.html", "assets/media/edits/a.png"],
        "historyEntryId": "entry-9",
    }
    log.write_text(
        "\n".join(
            [
                json.dumps(old),
                json.dumps({"type": "upsert", "job": {**new, "status": "running"}}),
                json.dumps({"type": "upsert", "job": new}),
                "not json",
                json.dumps({"type": "tombstone", "id": "gone", "projectPath": "x"}),
            ]
        )
    )

    runs = (await call(open_app, "catch_up")).structured_content["runs"]

    assert [r["number"] for r in runs] == [
        2,
        1,
    ]  # newest first; a later line for a run replaces the earlier
    assert runs[0]["status"] == "done" and runs[0]["history_entry"] == "entry-9"
    assert runs[0]["changed"] == ["index.html", "assets/media/edits/a.png"]
    assert len(runs[0]["instruction"]) <= 100

    since = (await call(open_app, "catch_up", {"since": "2026-10-04T00:00:00Z"})).structured_content["runs"]
    assert [r["number"] for r in since] == [2]


def git(root, *args):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


async def test_catch_up_with_the_app_closed_reads_the_disk_git_log_and_file_times(open_app, project):
    git(project, "init", "-q")
    git(project, "add", "index.html")
    git(project, "commit", "-q", "-m", "baseline compose")
    (project / "notes.txt").write_text("a hand edit")
    open_app.quit()

    found = (await call(open_app, "catch_up")).structured_content

    assert found["route"] == "disk" and "not running" in found["why"]
    assert any("baseline compose" in line for line in found["git"])
    assert "notes.txt" in [f["path"] for f in found["files"]]
    assert ".git" not in " ".join(f["path"] for f in found["files"])


async def test_without_git_the_disk_route_still_lists_files_and_says_no_git_log(open_app, project):
    open_app.quit()

    found = (await call(open_app, "catch_up", {"since": "2000-01-01T00:00:00Z"})).structured_content

    assert found["git"] is None
    assert "index.html" in [f["path"] for f in found["files"]]
    assert found["since"] == "2000-01-01T00:00:00+00:00"


async def test_a_project_the_app_does_not_list_goes_the_disk_route_too(tmp_path, project):
    other = FakeApp(tmp_path / "support-2", {"elsewhere": tmp_path})

    found = (await call(other, "catch_up")).structured_content

    assert found["route"] == "disk" and "open this project in the app" in found["why"]


async def test_a_since_the_app_cannot_read_is_its_refusal_not_a_silent_disk_fallback(
    open_app,
):
    result = await call(open_app, "catch_up", {"since": "last tuesday-ish"})

    assert result.is_error and "entry id, an ISO time" in text(result)


# ----- comments


async def test_a_comment_is_pinned_at_a_time_and_listed_back(open_app):
    posted = (await call(open_app, "post_comment", {"time": 61.0, "body": "credit covers the face"})).structured_content

    assert posted["number"] == 1 and posted["status"] == "waiting"
    assert posted["time"] == 61.0
    assert open_app.threads["demo"][0]["from"] == "hyperframes-mcp"  # filed under our author
    listed = (await call(open_app, "list_comments")).structured_content
    assert listed["count"] == 1
    assert listed["comments"][0] | {"id": None} == {
        "id": None,
        "number": 1,
        "time": 61.0,
        "status": "open",
        "text": "credit covers the face",
    }
    assert (await call(open_app, "list_comments", {"status": "resolved"})).structured_content["comments"] == []


async def test_a_handled_comment_is_resolved_by_its_number_and_can_be_opened_again(
    open_app,
):
    await call(open_app, "post_comment", {"time": 61.0, "body": "credit covers the face"})

    done = (await call(open_app, "resolve_comment", {"comment": 1})).structured_content

    assert (done["number"], done["status"], done["changed"]) == (1, "resolved", True)
    assert [
        c["number"]
        for c in (await call(open_app, "list_comments", {"status": "resolved"})).structured_content["comments"]
    ] == [1]
    again = (await call(open_app, "resolve_comment", {"comment": "#1"})).structured_content
    assert again["changed"] is False  # already resolved: nothing to change
    reopened = (await call(open_app, "resolve_comment", {"comment": 1, "reopen": True})).structured_content
    assert (reopened["status"], reopened["changed"]) == ("open", True)


async def test_a_comment_the_app_will_apply_says_what_to_use_and_what_it_costs(
    open_app,
):
    free = (
        await call(
            open_app,
            "post_comment",
            {
                "time": 9.0,
                "body": "add a counter 3 feet of brick",
                "layer": "l-p1-2",
                "change": "free",
            },
        )
    ).structured_content
    paid = (
        await call(
            open_app,
            "post_comment",
            {"time": 16.0, "body": "redraw the blast shot", "change": "paid"},
        )
    ).structured_content
    plain = await call(open_app, "post_comment", {"time": 20.0, "body": "a finding"})
    seen = await call(
        open_app,
        "post_comment",
        {"time": 21.0, "body": "the credit covers a face", "layer": "l-p1-2"},
    )

    first, second, third, fourth = (t["body"] for t in open_app.threads["demo"])
    assert (free["number"], paid["number"]) == (1, 2) and not plain.is_error
    assert first.startswith("add a counter 3 feet of brick")
    assert "Layer: l-p1-2." in first and "hyperframes-edit tools only" in first
    assert "never edit index.html by hand" in first
    assert "quote_picture or quote_clip, stop and say so" in first  # a free change stops at a cost
    assert "PAID" not in first and "leave this comment" in first
    assert "PAID" in second and "never edit index.html by hand" in second
    assert "quote first" in second and "do not call confirm_spend" in second
    assert "price" in second and "until they say yes" in second
    assert third == "a finding"  # a finding alone is posted as written
    assert fourth == "the credit covers a face\n\nLayer: l-p1-2."  # the layer is named, no rules
    assert not seen.is_error


async def test_a_layer_that_is_not_on_the_page_is_refused_and_nothing_is_posted(
    open_app,
):
    refused = await call(
        open_app,
        "post_comment",
        {"time": 9.0, "body": "move it", "layer": "l-vault-9", "change": "free"},
    )

    assert refused.is_error and "no layer 'l-vault-9'" in text(refused)
    assert open_app.threads.get("demo", []) == []


async def test_a_change_other_than_none_free_or_paid_is_refused(open_app):
    refused = await call(open_app, "post_comment", {"time": 1.0, "body": "x", "change": "maybe"})

    assert refused.is_error
    assert open_app.threads.get("demo", []) == []


async def test_a_comment_the_apps_agent_is_working_on_is_not_resolved(open_app):
    await call(open_app, "post_comment", {"time": 1.0, "body": "a note"})
    open_app.threads["demo"][0]["status"] = "in_progress"

    refused = await call(open_app, "resolve_comment", {"comment": 1})

    assert refused.is_error
    assert "ends that run" in text(refused) and "addressed" in text(refused)
    assert open_app.threads["demo"][0]["status"] == "in_progress"  # nothing changed
    reopened = await call(open_app, "resolve_comment", {"comment": 1, "reopen": True})
    assert reopened.is_error  # reopening one is no way round it: the next resolve would end the run
    assert open_app.threads["demo"][0]["status"] == "in_progress"
    open_app.threads["demo"][0]["status"] = "addressed"
    done = (await call(open_app, "resolve_comment", {"comment": 1})).structured_content
    assert done["changed"] is True  # once the run has ended the person may resolve it


async def test_a_comment_whose_run_is_queued_in_the_app_is_not_resolved_either(
    open_app,
):
    await call(open_app, "post_comment", {"time": 1.0, "body": "apply this"})
    await call(open_app, "post_comment", {"time": 2.0, "body": "and this"})
    thread = open_app.threads["demo"][0]  # still "open": the run has not started
    # b296 and later: a run carries every comment sent together as `threadIds`; earlier builds: one `threadId`
    for carried in ({"threadIds": [thread["id"]]}, {"threadId": thread["id"]}):
        waiting = {"id": "job-1", "kind": "comment"} | carried
        for status in ("queued", "running"):
            open_app.jobs["demo"] = [waiting | {"status": status}]
            refused = await call(open_app, "resolve_comment", {"comment": 1})
            assert refused.is_error and "ends that run" in text(refused), (
                carried,
                status,
            )
            assert thread["status"] == "open"  # nothing changed

    # reopening cancels nothing in the app (only resolving does), so a queued run does not stop it
    open_app.jobs["demo"] = [
        {
            "id": "job-1",
            "kind": "comment",
            "threadIds": [thread["id"]],
            "status": "queued",
        }
    ]
    reopened = await call(open_app, "resolve_comment", {"comment": 1, "reopen": True})
    assert not reopened.is_error

    # a run that carries two comments holds both: resolving either would end it, so the second is refused too
    both = {"id": "job-2", "kind": "comment", "threadIds": ["thread-1", "thread-2"]}
    open_app.jobs["demo"] = [both | {"status": "running"}]
    second = await call(open_app, "resolve_comment", {"comment": 2})
    assert second.is_error and "ends that run" in text(second)

    # a run that has not been taken up (waiting for the person's Send), one that ended, another comment's run
    for job in (
        both | {"status": "waiting"},
        both | {"status": "done"},
        both | {"status": "queued", "threadIds": ["thread-9"]},
        {"id": "job-3", "kind": "comment", "threadId": "thread-9", "status": "queued"},
    ):
        open_app.jobs["demo"] = [job]
        done = await call(open_app, "resolve_comment", {"comment": 1})
        assert not done.is_error, job
        thread["status"] = "open"


async def test_an_app_that_cannot_list_its_runs_still_resolves_a_comment_that_is_not_running(
    open_app,
):
    for answer in (None, lambda arguments, who: {"jobs": []}):
        await call(open_app, "post_comment", {"time": 1.0, "body": "a finding"})
        open_app.tool_agent_jobs = answer  # an older app (no such tool), or an answer that is not a list

        done = (await call(open_app, "resolve_comment", {"comment": len(open_app.threads["demo"])})).structured_content

        assert done["changed"] is True
        assert "queued" in done["note"]  # and it says the check could not be made


async def test_a_comment_that_does_not_exist_is_refused_with_the_numbers_there_are(
    open_app,
):
    await call(open_app, "post_comment", {"time": 1.0, "body": "a note"})

    refused = await call(open_app, "resolve_comment", {"comment": 9})

    assert refused.is_error and "the numbers are: 1" in text(refused)


async def test_resolving_a_comment_with_the_app_closed_says_so(open_app):
    open_app.quit()

    refused = await call(open_app, "resolve_comment", {"comment": 1})

    assert refused.is_error and "not running" in text(refused)
    assert "the comment was not changed" in text(refused)


async def test_with_the_app_closed_a_comment_is_not_faked(open_app):
    open_app.quit()

    posted = await call(open_app, "post_comment", {"time": 5.0, "body": "x"})
    listed = await call(open_app, "list_comments")

    for refused in (posted, listed):
        assert refused.is_error and "not running" in text(refused)
    assert "no comment was posted" in text(posted) and "tell them in your reply" in text(posted)
    assert open_app.threads == {}


async def test_an_empty_or_huge_comment_is_refused_before_it_reaches_the_app(open_app):
    for body in ("  ", "x" * 4001):
        result = await call(open_app, "post_comment", {"time": 5.0, "body": body})
        assert result.is_error and "1 to 4000 characters" in text(result)
    assert open_app.calls == []


async def test_a_comment_too_long_with_its_apply_line_says_how_much_room_it_has(
    open_app,
):
    body = "x" * 3900

    refused = await call(open_app, "post_comment", {"time": 5.0, "body": body, "change": "paid"})

    assert refused.is_error
    message = text(refused)
    assert "its layer and apply line: here they take" in message
    room = int(re.search(r"body is at most (\d+)", message)[1])
    assert 3500 < room < 3900
    assert open_app.calls == []
    ok = await call(
        open_app,
        "post_comment",
        {"time": 5.0, "body": "x" * room, "change": "paid"},
    )
    assert not ok.is_error  # the room it names is the room there is


async def test_a_listed_change_comment_shows_its_words_not_the_apply_boilerplate(
    open_app,
):
    for change in ("free", "paid"):
        await call(
            open_app,
            "post_comment",
            {
                "time": 9.0,
                "body": "redraw the blast shot with smoke in the doorway",
                "layer": "l-p1-2",
                "change": change,
            },
        )

    listed = (await call(open_app, "list_comments")).structured_content["comments"]

    assert [c["text"] for c in listed] == ["redraw the blast shot with smoke in the doorway Layer: l-p1-2."] * 2


async def test_a_long_list_of_comments_is_cut_to_the_newest(open_app):
    for n in range(35):
        await call(open_app, "post_comment", {"time": float(n), "body": f"note {n}"})

    listed = (await call(open_app, "list_comments")).structured_content

    assert listed["count"] == 35 and len(listed["comments"]) == 30
    assert listed["comments"][0]["text"] == "note 34"


# ----- all of them


async def test_the_tools_are_registered_and_none_shows_the_apps_token(open_app):
    async with Client(build_server(AppBridge(open_app.support, transport=open_app.transport))) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"resolve_selection", "catch_up", "post_comment", "list_comments"} <= set(tools)
    assert tools["catch_up"].annotations.read_only_hint is True
    assert tools["post_comment"].annotations.read_only_hint is False

    open_app.select("demo", currentTime=1.0, target={"id": "l-p1-1"})
    seen = [
        await call(open_app, "resolve_selection", {"use_app": True}),
        await call(open_app, "catch_up"),
        await call(open_app, "post_comment", {"time": 1.0, "body": "a note"}),
        await call(open_app, "list_comments"),
        await call(open_app, "post_comment", {"time": 1.0, "body": ""}),
    ]

    assert TOKEN not in "".join(str(result) for result in seen)
