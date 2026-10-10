"""The tools that change the page, as the app's agent calls them (an in-memory MCP client), against the fake app and the fake
CLI: a timeline batch (dry run and applied), one variable, a picture, and undo. Each edit is one history entry of
``hyperframes-mcp``, in the app's log when the app is up and in the CLI's when it is not; the person's own edits are never
undone. ``tests/server/test_edit_real_cli.py`` runs the same edits on the real pinned CLI."""

from __future__ import annotations

import json
import re

import pytest
from mcp import Client

from hyperframes_mcp.motion import manifest, project_edit
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.server import build_server
from tests.motion.test_project_edit import composed, two_shots
from tests.server.fake_app import FakeApp
from tests.server.test_app_tools import call, text
from tests.server.test_vector_tools import ARROW

pytestmark = pytest.mark.asyncio

TRIM = {"verb": "trim", "layer": "l-p1-2", "end": 6}
MOVE = {"verb": "move", "layer": "l-m1-2", "time": 9}


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project of two shots (the photo 0 to 8 s, the clip 8 to 16 s, 20 s long), the session's folder."""
    root = composed(tmp_path, two_shots())[0]
    manifest.write_manifest(root, manifest.ProjectManifest(profile="history"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def open_app(tmp_path, project):
    """The fake app, open on the project as ``demo``."""
    return FakeApp(tmp_path / "support", {"demo": project})


def page_of(project) -> str:
    return (project / "index.html").read_text(encoding="utf-8")


def layer(project, layer_id: str):
    return project_edit.mounts(page_of(project))[layer_id].layer


def tools(app) -> list[str]:
    """The app tools the fake saw, in order, without the probes."""
    return [c.tool for c in app.calls if c.tool != "project.list"]


def entries_of(app, author="hyperframes-mcp") -> list[dict]:
    return [e for e in app.history.get("demo", []) if e["who"]["name"] == author]


# ----- edit_timeline


async def test_a_dry_run_says_what_would_change_and_changes_nothing(open_app, project, hyperframes):
    before = page_of(project)

    result = await call(open_app, "edit_timeline", {"ops": [TRIM, MOVE]})

    found = result.structured_content
    assert found["dry_run"] is True and found["applied"] is False
    assert "dry_run=false" in found["next"] and "NOT applied" in found["next"]
    assert found["changes"] == [
        {"layer": "l-p1-2", "was": [0.3, 8.0], "now": [0.3, 6.0]},
        {"layer": "l-m1-2", "was": [8.3, 16.0], "now": [9.0, 16.7]},
    ]
    assert page_of(project) == before
    assert hyperframes.calls == [["timeline", "apply", "-", "--json", "--plan"]]
    assert open_app.calls == [] and open_app.history == {}


async def test_an_applied_batch_is_one_entry_of_hyperframes_mcp_in_the_apps_history(open_app, project, hyperframes):
    result = await call(open_app, "edit_timeline", {"ops": [TRIM, MOVE], "dry_run": False})

    found = result.structured_content
    assert not result.is_error and found["route"] == "app" and found["applied"] is True
    assert [c["layer"] for c in found["changes"]] == ["l-p1-2", "l-m1-2"]
    # the page: the new times, and each block's length is its new slot
    trimmed, moved = layer(project, "l-p1-2"), layer(project, "l-m1-2")
    assert (trimmed.start, trimmed.end, trimmed.values["durationSeconds"]) == (
        0.3,
        6.0,
        5.7,
    )
    assert (moved.start, moved.end) == (9.0, 16.7)
    # one entry, ours, labelled, with the page in it; the app's own tools did the writing around the CLI's step
    (entry,) = entries_of(open_app)
    assert entry["label"] == "Timeline: trim l-p1-2, move l-m1-2" and list(entry["files"]) == ["index.html"]
    assert found["history_entry"] == entry["id"]
    assert tools(open_app) == [
        "history.begin",
        "file.read",
        "file.write",
        "history.end",
    ]
    assert [c[:2] for c in hyperframes.calls] == [["timeline", "apply"]] * 2  # the plan, then the write
    assert "run_checks" in found["next"] and "get_frame at " in found["next"]
    assert all(call.user_agent == "hyperframes-mcp/1" for call in open_app.calls)


async def test_an_app_busy_for_a_moment_is_still_the_app_route_not_the_disk(open_app, project, hyperframes):
    # seen live: the probe after an edit the app had just reloaded timed out, and the next edit would have gone to disk
    open_app.busy_for = 1

    result = await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    assert not result.is_error and result.structured_content["route"] == "app"
    (entry,) = entries_of(open_app)
    assert result.structured_content["history_entry"] == entry["id"]
    assert not [c for c in hyperframes.calls if c[:2] == ["history", "begin"]]


async def test_the_app_closed_goes_the_disk_route_through_the_clis_history(open_app, project, hyperframes):
    open_app.quit()

    result = await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    assert result.structured_content["route"] == "disk"
    (entry,) = [e for e in hyperframes.history(project).entries if e["who"]["name"] == "hyperframes-mcp"]
    assert entry["label"] == "Timeline: trim l-p1-2" and list(entry["files"]) == ["index.html"]
    assert result.structured_content["history_entry"] == entry["id"]
    assert layer(project, "l-p1-2").end == 6.0
    assert [c[:2] for c in hyperframes.calls] == [
        ["timeline", "apply"],
        ["history", "begin"],
        ["timeline", "apply"],
        ["history", "end"],
    ]


async def test_a_split_layer_gets_our_id_and_the_other_tools_read_it(open_app, project):
    split = {"verb": "split", "layer": "l-p1-1", "time": 4}

    result = await call(open_app, "edit_timeline", {"ops": [split], "dry_run": False})

    changes = {c["layer"]: c for c in result.structured_content["changes"]}
    assert changes["l-p1-3"]["made_from"] == "l-p1-1" and changes["l-p1-3"]["now"] == [
        4.0,
        8.0,
    ]
    second = layer(project, "l-p1-3")
    assert (second.component, second.start, second.end) == ("photo-shot", 4.0, 8.0)
    assert [layer(project, i).values["durationSeconds"] for i in ("l-p1-1", "l-p1-3")] == [4.0, 4.0]
    table = (await call(open_app, "layer_table")).structured_content["layers"]
    assert [row["id"] for row in table if row["kind"] == "photo-shot"] == [
        "l-p1-1",
        "l-p1-3",
    ]
    # the new layer is a picture like any other: it can be given another file
    replaced = await call(
        open_app,
        "replace_media",
        {"layer": "l-p1-3", "file": str(picture_in(project)), "credit": "NARA"},
    )
    assert not replaced.is_error and replaced.structured_content["credit_layer"] == "l-p1-2"


def picture_in(project):
    path = project / "new.png"
    path.write_bytes(b"a new picture")
    return path


async def test_the_clis_own_refusal_reaches_the_agent_with_its_fix_and_nothing_is_written(
    open_app, project, hyperframes
):
    before = page_of(project)

    result = await call(
        open_app,
        "edit_timeline",
        {"ops": [{"verb": "move", "layer": "l-p1-2", "time": 1}], "dry_run": False},
    )

    assert result.is_error
    assert "would overlap #l-m1-2" in text(result) and "pass --overwrite or move the named neighbour" in text(result)
    assert page_of(project) == before and open_app.history == {}
    assert [c[:2] for c in hyperframes.calls] == [["timeline", "apply"]]  # the plan only


async def test_an_op_that_cannot_be_right_is_refused_before_the_cli_runs(open_app, project, hyperframes):
    for op, words in (
        ({"verb": "move", "layer": "l-x9-1", "time": 1}, "no layer 'l-x9-1'"),
        (
            {"verb": "trim", "layer": "l-p1-2", "duration": -2},
            "duration must be above 0",
        ),
        ({"verb": "trim", "layer": "l-m1-2", "end": 99}, "past the end of the video"),
    ):
        result = await call(open_app, "edit_timeline", {"ops": [op], "dry_run": False})
        assert result.is_error and words in text(result)

    assert hyperframes.calls == [] and open_app.calls == []


async def test_a_duplicate_that_pushes_a_layer_past_the_end_is_refused_with_the_layer_named(open_app, project):
    before = page_of(project)

    result = await call(
        open_app,
        "edit_timeline",
        {"ops": [{"verb": "duplicate", "layer": "l-p1-2", "at": 8}], "dry_run": False},
    )

    assert result.is_error
    assert "l-m1-2 would end at 23.7 s, after the end of the video (20 s)" in text(result)
    assert page_of(project) == before


async def test_a_trim_below_what_the_block_needs_is_refused(open_app, project):
    result = await call(
        open_app,
        "edit_timeline",
        {"ops": [{"verb": "trim", "layer": "l-p1-2", "end": 1}], "dry_run": False},
    )

    assert result.is_error and "shorter than the block's minimum of 2.4 s" in text(result)


async def test_a_page_the_person_edits_while_it_is_written_is_read_again_once_and_kept(open_app, project):
    def person_adds_a_note(project_id, path):
        open_app.after_read = None
        content = (project / path).read_text(encoding="utf-8")
        open_app.person_edit(project_id, path, content + "<!-- the person's note -->\n")

    open_app.after_read = person_adds_a_note

    result = await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    assert not result.is_error
    assert page_of(project).count("the person's note") == 1 and layer(project, "l-p1-2").end == 6.0
    assert tools(open_app) == [
        "history.begin",
        "file.read",
        "file.write",
        "file.read",
        "file.write",
        "history.end",
    ]


async def test_a_page_that_keeps_changing_fails_clearly_after_one_retry(open_app, project):
    notes = iter(range(100))
    open_app.after_read = lambda project_id, path: open_app.person_edit(
        project_id,
        path,
        (project / path).read_text(encoding="utf-8") + f"<!-- {next(notes)} -->\n",
    )

    result = await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    assert result.is_error
    assert "changed twice while it was being written" in text(result) and "call undo" in text(result)
    assert tools(open_app).count("file.write") == 2 and tools(open_app)[-1] == "history.end"


async def test_a_history_that_cannot_be_opened_stops_the_edit_before_it_writes(open_app, project, hyperframes):
    open_app.quit()
    hyperframes.refuse_history = "the HyperFrames app has this project open"
    before = page_of(project)

    result = await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    assert result.is_error and "history entry could not be opened" in text(result)
    assert "the HyperFrames app has this project open" in text(result)
    assert page_of(project) == before


# ----- set_variable


async def test_a_variable_is_set_in_one_history_entry_and_the_label_follows(open_app, project):
    result = await call(
        open_app,
        "set_variable",
        {"layer": "l-p1-2", "name": "text", "value": "Bundesarchiv, Bild 183"},
    )

    found = result.structured_content
    assert found["old"] == "US Army Signal Corps, NARA 111-SC-204515" and found["route"] == "app"
    assert layer(project, "l-p1-2").values["text"] == "Bundesarchiv, Bild 183"
    assert 'data-timeline-label="p1 · Credit: Bundesarchiv, Bild 183"' in page_of(project)
    (entry,) = entries_of(open_app)
    assert entry["label"] == "Set text of l-p1-2"


async def test_a_name_or_value_the_block_does_not_take_is_refused_before_any_history_is_opened(
    open_app, project, hyperframes
):
    before = page_of(project)
    for arguments, words in (
        ({"layer": "l-p1-2", "name": "text", "value": "x" * 200}, "at most 140"),
        ({"layer": "l-p1-2", "name": "align", "value": "up"}, "one of left, right"),
        ({"layer": "l-p1-2", "name": "colour", "value": "red"}, "takes: align, text"),
        ({"layer": "l-p1-2", "name": "durationSeconds", "value": 3}, "edit_timeline"),
        ({"layer": "l-p1-2", "name": "text", "value": ""}, "no credit line"),
        ({"layer": "l-p1-1", "name": "image", "value": "x.jpg"}, "replace_media"),
    ):
        result = await call(open_app, "set_variable", arguments)
        assert result.is_error and words in text(result), arguments

    assert page_of(project) == before
    assert open_app.calls == [] and hyperframes.calls == []


async def test_a_variable_set_with_the_app_closed_is_an_entry_in_the_clis_history(open_app, project, hyperframes):
    open_app.quit()

    result = await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})

    assert result.structured_content["route"] == "disk"
    assert layer(project, "l-p1-2").values["align"] == "right"
    (entry,) = [e for e in hyperframes.history(project).entries if e["who"]["name"] == "hyperframes-mcp"]
    assert entry["label"] == "Set align of l-p1-2"


# ----- replace_media


async def test_replacing_media_is_one_entry_that_holds_the_page_and_the_new_file(open_app, project):
    result = await call(
        open_app,
        "replace_media",
        {"layer": "l-p1-1", "file": str(picture_in(project)), "credit": "NARA"},
    )

    assert not result.is_error and result.structured_content["route"] == "app"
    (entry,) = entries_of(open_app)
    assert sorted(entry["files"]) == ["assets/media/edits/new.png", "index.html"]
    assert layer(project, "l-p1-1").values["image"] == "assets/media/edits/new.png"


# ----- add_graphic


async def test_a_graphic_is_one_history_entry_of_ours_with_the_block_and_the_page_and_undo_takes_it_back(
    open_app, project
):
    before = page_of(project)
    await call(open_app, "save_vector", {"svg": ARROW, "name": "arrow"})

    result = await call(open_app, "add_graphic", {"vector": "arrow", "start": 2, "duration": 4})

    added = result.structured_content
    assert not result.is_error, text(result)
    assert added["route"] == "app"
    (entry,) = entries_of(open_app)
    assert entry["label"] == "Add graphic arrow at 2 s"
    assert added["history_entry"] == entry["id"]
    # the page went through the app's checked write; the block it needs came with it, inside the same entry
    assert "index.html" in entry["files"] and "compositions/graphic-stamp.html" in entry["files"]
    assert added["layer"] in project_edit.mounts(page_of(project))

    undone = (await call(open_app, "undo")).structured_content

    assert undone["undone"] == ["Add graphic arrow at 2 s"] and undone["left"] == 0
    assert page_of(project) == before


# ----- undo


async def test_undo_takes_back_our_last_edit_and_says_which(open_app, project):
    before = page_of(project)
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    first = (await call(open_app, "undo")).structured_content
    second = (await call(open_app, "undo")).structured_content

    assert first["undone"] == ["Timeline: trim l-p1-2"] and first["left"] == 1 and first["route"] == "app"
    assert second["undone"] == ["Set align of l-p1-2"] and second["left"] == 0
    assert page_of(project) == before


async def test_undo_with_steps_takes_the_newest_first_and_stops_when_none_are_left(open_app, project):
    before = page_of(project)
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False})

    done = (await call(open_app, "undo", {"steps": 3})).structured_content

    assert done["undone"] == ["Timeline: trim l-p1-2", "Set align of l-p1-2"]
    assert done["stopped"] == "nothing of yours is left to undo" and done["left"] == 0
    assert page_of(project) == before


async def test_undo_with_nothing_of_ours_says_so(open_app, project):
    open_app.person_edit("demo", "index.html", page_of(project) + "<!-- the person's -->\n")

    result = await call(open_app, "undo")

    assert result.is_error and "nothing of yours is left to undo" in text(result)
    assert "the person's" in page_of(project)


async def test_undo_leaves_the_persons_edit_to_the_same_file_alone_and_says_who_made_it(open_app, project):
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    open_app.person_edit("demo", "index.html", page_of(project) + "<!-- the person's -->\n")
    kept = page_of(project)

    result = await call(open_app, "undo")

    assert result.is_error
    assert "nothing was undone" in text(result) and "index.html changed after your edit, by the person" in text(result)
    assert page_of(project) == kept
    assert len(entries_of(open_app)) == 1  # no undo entry was made


async def test_an_undo_the_app_declines_is_reported_in_its_words(open_app, project):
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    waiting = "Undo is available when the other run in this project finishes."
    open_app.tool_history_undo = lambda arguments, who: {
        "ok": False,
        "reason": waiting,
        "stillUndone": [],
    }

    result = await call(open_app, "undo")

    assert result.is_error and waiting in text(result)
    assert layer(project, "l-p1-2").values["align"] == "right"


async def test_an_empty_batch_is_refused(open_app, project, hyperframes):
    result = await call(open_app, "edit_timeline", {"ops": []})

    assert result.is_error and "no ops" in text(result) and hyperframes.calls == []


async def test_undo_goes_through_when_the_person_changed_only_another_file(open_app, project):
    before = page_of(project)
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    open_app.person_edit("demo", "VIDEO.md", "a note\n")

    result = await call(open_app, "undo")

    assert not result.is_error and page_of(project) == before
    assert (project / "VIDEO.md").read_text() == "a note\n"


async def test_undo_with_the_app_closed_uses_the_clis_history_and_refuses_a_hand_edit_on_top(
    open_app, project, hyperframes
):
    open_app.quit()
    before = page_of(project)
    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})

    undone = (await call(open_app, "undo")).structured_content
    assert undone["route"] == "disk" and undone["undone"] == ["Set align of l-p1-2"]
    assert page_of(project) == before

    await call(open_app, "set_variable", {"layer": "l-p1-2", "name": "align", "value": "right"})
    (project / "index.html").write_text(page_of(project) + "<!-- by hand -->\n", encoding="utf-8")
    refused = await call(open_app, "undo")

    assert refused.is_error and "by something outside the app" in text(refused)
    assert "<!-- by hand -->" in page_of(project)


async def test_the_number_of_steps_is_bounded(open_app, project):
    for steps in (0, 11):
        result = await call(open_app, "undo", {"steps": steps})
        assert result.is_error and "steps is 1 to 10" in text(result)


# ----- all of them


async def test_the_edit_tools_are_registered_as_writing_tools_and_none_shows_the_apps_token(open_app, project):
    async with Client(build_server(AppBridge(open_app.support, transport=open_app.transport))) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name in ("edit_timeline", "set_variable", "undo", "replace_media"):
        assert listed[name].annotations.read_only_hint is False, name
        assert listed[name].annotations.destructive_hint is False, name

    results = [
        await call(open_app, "edit_timeline", {"ops": [TRIM], "dry_run": False}),
        await call(open_app, "undo"),
        await call(open_app, "undo"),
    ]

    from tests.server.fake_app import TOKEN

    assert TOKEN not in json.dumps([str(r) for r in results]) and re.search(r"Bearer", str(results)) is None


# ----- add_block


async def test_a_block_is_added_in_one_history_entry_with_its_block_installed(open_app, project):
    result = await call(
        open_app,
        "add_block",
        {
            "component": "counter",
            "start": 2.0,
            "duration": 5.4,
            "values": {
                "from": 0,
                "to": 3,
                "prefix": "",
                "suffix": " feet",
                "decimals": 0,
                "label": "of brick",
            },
        },
    )

    found = result.structured_content
    assert not result.is_error and found["route"] == "app"
    assert (found["layer"], found["shot"], found["component"]) == (
        "l-p1-3",
        "p1",
        "counter",
    )
    assert layer(project, "l-p1-3").values["label"] == "of brick"
    (entry,) = entries_of(open_app)
    assert entry["label"] == "Add counter at 2 s"
    # the entry holds the page and the block it brought with its fonts: undo takes all of it back
    assert {"index.html", "compositions/counter.html"} <= set(entry["files"])
    assert "get_frame" in found["next"] and "run_checks" in found["next"]


async def test_a_block_that_cannot_be_added_is_refused_before_any_history_is_opened_or_file_written(
    open_app, project, hyperframes
):
    before = page_of(project)
    for arguments, words in (
        ({"component": "date-plate", "start": 1, "duration": 5}, "no block"),
        ({"component": "photo-shot", "start": 1, "duration": 5}, "replace_media"),
        (
            {"component": "counter", "start": 1, "duration": 5.4, "values": {"x": 1}},
            "counter takes:",
        ),
        (
            {
                "component": "counter",
                "start": 99,
                "duration": 5.4,
                "values": {
                    "from": 0,
                    "to": 3,
                    "prefix": "",
                    "suffix": "",
                    "decimals": 0,
                    "label": "x",
                },
            },
            "past the end",
        ),
        # a block's default is a sample: a credit that was never written must not go on screen as a credit
        ({"component": "credit-line", "start": 1, "duration": 6}, "give text"),
    ):
        result = await call(open_app, "add_block", arguments)
        assert result.is_error and words in text(result), arguments

    assert page_of(project) == before
    assert not (project / "compositions/counter.html").exists()
    assert open_app.calls == [] and hyperframes.calls == []


async def test_a_block_added_with_the_app_closed_is_an_entry_in_the_clis_history(open_app, project, hyperframes):
    open_app.quit()

    result = await call(
        open_app,
        "add_block",
        {
            "component": "date-stamp",
            "start": 2.0,
            "duration": 5.0,
            "values": {"text": "April 1945"},
        },
    )

    assert not result.is_error and result.structured_content["route"] == "disk"
    assert layer(project, "l-p1-3").values["text"] == "April 1945"
    assert (project / "compositions/date-stamp.html").is_file()
    (entry,) = [e for e in hyperframes.history(project).entries if e["who"]["name"] == "hyperframes-mcp"]
    assert entry["label"] == "Add date-stamp at 2 s"


# ----- arrange_layer


async def test_a_layer_is_moved_in_one_history_entry_and_reset_is_another(open_app, project):
    moved = (await call(open_app, "arrange_layer", {"layer": "l-p1-2", "dy": 200, "scale": 0.8})).structured_content

    assert moved["route"] == "app" and moved["now"] == {
        "dx": 0,
        "dy": 200,
        "scale": 0.8,
    }
    assert "translate: 0px 200px" in page_of(project)
    reset = (await call(open_app, "arrange_layer", {"layer": "l-p1-2", "reset": True})).structured_content
    assert reset["now"] == {"dx": 0, "dy": 0, "scale": 1}
    assert "translate" not in page_of(project)
    assert [e["label"] for e in entries_of(open_app)] == [
        "Move l-p1-2",
        "Move l-p1-2",
    ]
    assert "get_frame" in moved["next"]


async def test_a_move_that_makes_no_sense_is_refused_before_any_history_is_opened(open_app, project):
    before = page_of(project)
    for arguments, words in (
        ({"layer": "l-p1-1", "dx": 5}, "fills the frame"),
        ({"layer": "l-p1-2"}, "say what to change"),
        ({"layer": "l-p1-2", "scale": 9}, "between 0.2 and 2"),
    ):
        result = await call(open_app, "arrange_layer", arguments)
        assert result.is_error and words in text(result), arguments

    assert page_of(project) == before and open_app.calls == []
