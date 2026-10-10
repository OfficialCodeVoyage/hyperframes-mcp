"""``src/hyperframes_mcp/motion/hf_cli.py``: the pinned HyperFrames CLI run on one project, with a fake in place of node.

The fake stands where ``subprocess.run`` is called, so each test sees the command, the folder it ran in, the stdin and
the environment, and answers as the real CLI does (the shapes below are what 0.8.139 printed for a composed project).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperframes_mcp.hfcli.cli import QUIET_ENV, cli_script
from hyperframes_mcp.motion import hf_cli

META = {"version": "0.8.139", "latestVersion": "0.8.139", "updateAvailable": False}


def row(id_: str, start: float, end: float, *, kind: str = "graphics", **more) -> dict:
    """A timeline row as the CLI prints it (the fields these tests and the wrappers read)."""
    return {
        "id": id_,
        "trackKind": kind,
        "start": start,
        "end": end,
        "duration": end - start,
        "file": "index.html",
        "ref": f"#{id_}",
        "hfId": f"hf-{id_}",
        **more,
    }


class FakeNode:
    """Stands in for ``subprocess.run``: answers each call in turn and records how it was called."""

    def __init__(self, *answers: subprocess.CompletedProcess | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[SimpleNamespace] = []

    def __call__(self, command, **kwargs):
        self.calls.append(
            SimpleNamespace(
                node=command[0],
                script=Path(command[1]),
                argv=command[2:],
                cwd=kwargs["cwd"],
                stdin=kwargs["input"],
                env=kwargs["env"],
                timeout=kwargs["timeout"],
            )
        )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def printed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def prints_json(payload: dict, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return printed(returncode, json.dumps({**payload, "_meta": META}, indent=2), stderr)


@pytest.fixture
def project(tmp_path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


@pytest.fixture
def node_dir(tmp_path) -> Path:
    return tmp_path / "node"


def use(monkeypatch, *answers) -> FakeNode:
    fake = FakeNode(*answers)
    monkeypatch.setattr(hf_cli.subprocess, "run", fake)
    return fake


# -- running it --------------------------------------------------------------------------------------


def test_every_call_runs_the_pinned_cli_inside_the_project_with_telemetry_off(monkeypatch, project, node_dir):
    fake = use(monkeypatch, prints_json({"timeline": {"duration": 6.0, "tracks": []}}))

    hf_cli.timeline(project, node_dir=node_dir)

    (call,) = fake.calls
    assert call.script == cli_script(node_dir)
    assert call.cwd == project
    assert call.argv == ["timeline", "--json"]
    assert all(call.env[name] == value for name, value in QUIET_ENV.items())


def test_a_cli_that_cannot_start_or_hangs_is_a_hyperframes_error(monkeypatch, project, node_dir):
    use(
        monkeypatch,
        FileNotFoundError("node"),
        subprocess.TimeoutExpired("node", 120),
    )

    with pytest.raises(hf_cli.HyperframesError, match="node"):
        hf_cli.lint(project, node_dir=node_dir)
    with pytest.raises(hf_cli.HyperframesError, match="lint ran past 120 s"):
        hf_cli.lint(project, node_dir=node_dir)


def test_the_installed_version_is_read_from_the_package_or_is_none(node_dir):
    assert hf_cli.version(node_dir) is None

    package = cli_script(node_dir).parents[1] / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"name": "hyperframes", "version": "0.8.139"}))

    assert hf_cli.version(node_dir) == "0.8.139"


# -- timeline ----------------------------------------------------------------------------------------


def test_timeline_returns_the_tracks_without_the_meta(monkeypatch, project, node_dir):
    tracks = [{"kind": "graphics", "rows": [row("l-lift-1", 0, 6.78)]}]
    use(monkeypatch, prints_json({"timeline": {"duration": 18.0, "tracks": tracks}}))

    answer = hf_cli.timeline(project, node_dir=node_dir)

    assert answer == {"timeline": {"duration": 18.0, "tracks": tracks}}


def test_a_cli_that_prints_no_json_is_an_error_with_its_last_line(monkeypatch, project, node_dir):
    use(monkeypatch, printed(1, stderr="Error: no index.html\nnode crashed"))

    with pytest.raises(hf_cli.HyperframesError, match="timeline failed: node crashed"):
        hf_cli.timeline(project, node_dir=node_dir)


# -- apply -------------------------------------------------------------------------------------------

OPS = [
    {"verb": "move", "ref": "#l-lift-1", "time": "1"},
    {"verb": "trim", "ref": "#l-lift-2", "end": "9"},
]


def applied(before: list[dict], after: list[dict], **more) -> subprocess.CompletedProcess:
    return prints_json(
        {
            "ok": True,
            "planned": True,
            "receipt": None,
            "file": ["index.html"],
            "before": before,
            "after": after,
            "diff": "--- before\n+++ after\n",
            "warnings": [],
            **more,
        }
    )


def test_apply_sends_the_ops_on_stdin_and_plans_on_a_dry_run(monkeypatch, project, node_dir):
    fake = use(monkeypatch, applied([], []))

    hf_cli.apply(project, OPS, dry_run=True, node_dir=node_dir)

    (call,) = fake.calls
    assert call.argv == ["timeline", "apply", "-", "--json", "--plan"]
    assert json.loads(call.stdin) == OPS
    assert call.cwd == project


def test_apply_without_dry_run_writes_and_returns_the_receipt(monkeypatch, project, node_dir):
    receipt = [{"file": "index.html", "version": "v2", "backupPath": ".hyperframes/b"}]
    fake = use(monkeypatch, applied([], [], planned=False, receipt=receipt))

    answer = hf_cli.apply(project, OPS, dry_run=False, node_dir=node_dir)

    assert fake.calls[0].argv == ["timeline", "apply", "-", "--json"]
    assert answer["receipt"] == receipt and answer["planned"] is False


def test_apply_returns_only_the_rows_the_ops_touched(monkeypatch, project, node_dir):
    others = [row(f"l-s{n:02d}-1", n, n + 1) for n in range(200)]
    lift1, lift2 = row("l-lift-1", 0, 6.78), row("l-lift-2", 6.78, 8)
    moved, trimmed = row("l-lift-1", 1, 7.78), row("l-lift-2", 6.78, 9)
    use(
        monkeypatch,
        applied(
            [lift1, *others, lift2],
            [*others, moved, trimmed],
            warnings=["l-lift-1 now overlaps a credit"],
        ),
    )

    answer = hf_cli.apply(project, OPS, dry_run=True, node_dir=node_dir)

    assert answer["before"] == [lift1, lift2]
    assert answer["after"] == [moved, trimmed]
    assert answer["warnings"] == ["l-lift-1 now overlaps a credit"]
    assert answer["file"] == ["index.html"] and answer["diff"].startswith("--- before")
    assert "_meta" not in answer


def test_apply_returns_the_rows_a_split_made_and_a_delete_removed(monkeypatch, project, node_dir):
    lift, gone = row("l-lift-1", 0, 6), row("l-gone-1", 6, 7)
    first, second = row("l-lift-1", 0, 3), row("l-lift-1-2", 3, 6)
    unrelated = row("l-other-1", 8, 9)
    use(
        monkeypatch,
        applied([lift, gone, unrelated], [first, second, unrelated]),
    )
    ops = [
        {"verb": "split", "ref": "#l-lift-1", "time": "3"},
        {"verb": "delete", "ref": "hf:hf-l-gone-1"},
    ]

    answer = hf_cli.apply(project, ops, dry_run=True, node_dir=node_dir)

    assert answer["before"] == [lift, gone]
    assert answer["after"] == [first, second]


def test_apply_also_returns_a_row_an_op_pushed_without_naming_it(monkeypatch, project, node_dir):
    # a duplicate on a track pushes the later clips of that track on (the CLI's insert), named by no op
    copy_source, pushed = row("l-lift-2", 0, 5.7), row("l-credit-1", 6.3, 14)
    nested = row("img", 0, 3, ref="graphics/4", nested=True)
    use(
        monkeypatch,
        applied(
            [copy_source, pushed, nested, row("l-still-1", 1, 2)],
            [
                copy_source,
                row("l-lift-2-copy", 0, 5.7),
                row("l-credit-1", 12, 19.7),
                nested,
                row("l-still-1", 1, 2),
            ],
        ),
    )

    answer = hf_cli.apply(
        project,
        [{"verb": "duplicate", "ref": "#l-lift-2", "at": "0"}],
        dry_run=True,
        node_dir=node_dir,
    )

    assert [r["id"] for r in answer["after"]] == [
        "l-lift-2",
        "l-lift-2-copy",
        "l-credit-1",
    ]
    assert answer["before"] == [copy_source, pushed]


def test_apply_finds_a_row_by_its_hyperframes_id_too(monkeypatch, project, node_dir):
    stamped = row("", 0, 2, ref="hf:hf-abc", hfId="hf-abc")
    moved = row("", 1, 3, ref="hf:hf-abc", hfId="hf-abc")
    use(monkeypatch, applied([stamped, row("l-a-1", 5, 6)], [moved, row("l-a-1", 5, 6)]))

    answer = hf_cli.apply(
        project,
        [{"verb": "move", "ref": "hf:hf-abc", "time": "1"}],
        dry_run=True,
        node_dir=node_dir,
    )

    assert answer["before"] == [stamped] and answer["after"] == [moved]


def test_a_refused_plan_raises_with_the_reason_and_the_fix(monkeypatch, project, node_dir):
    refusal = {
        "ok": False,
        "reason": "timeline apply: #l-nope-9 was not found",
        "fix": 'add id="<name>" to the element in its source file, then retry with #<name>',
    }
    use(monkeypatch, printed(2, "", json.dumps(refusal, indent=2)))

    with pytest.raises(hf_cli.HyperframesError) as caught:
        hf_cli.apply(
            project,
            [{"verb": "move", "ref": "#l-nope-9", "time": "1"}],
            dry_run=True,
            node_dir=node_dir,
        )

    assert caught.value.reason == refusal["reason"]
    assert caught.value.fix == refusal["fix"]
    assert str(caught.value) == f"{refusal['reason']}; {refusal['fix']}"


def test_a_refusal_survives_a_log_line_before_it_on_stderr(monkeypatch, project, node_dir):
    stderr = '[hyperframes] note\n{"ok": false, "reason": "no", "fix": "yes"}'
    use(monkeypatch, printed(2, "", stderr))

    with pytest.raises(hf_cli.HyperframesError) as caught:
        hf_cli.apply(project, OPS, dry_run=True, node_dir=node_dir)

    assert (caught.value.reason, caught.value.fix) == ("no", "yes")


# -- history -----------------------------------------------------------------------------------------

ENTRY = {
    "id": "e1",
    "who": {"kind": "agent", "name": "hyperframes-mcp"},
    "label": "Move l-lift-1",
    "files": ["index.html"],
    "undone": False,
}


def test_a_turn_is_begun_and_ended_under_the_authors_name(monkeypatch, project, node_dir):
    fake = use(
        monkeypatch,
        prints_json({"entryId": "e1"}),
        prints_json({"entry": ENTRY, "parts": ["e1"]}),
        prints_json({"entry": None, "parts": []}),
    )

    started = hf_cli.history_begin(project, "hyperframes-mcp", "Move l-lift-1", node_dir=node_dir)
    ended = hf_cli.history_end(project, node_dir=node_dir)
    nothing = hf_cli.history_end(project, node_dir=node_dir)

    assert (started, ended, nothing) == ("e1", ENTRY, None)
    assert fake.calls[0].argv == [
        "history", "begin", "--who", "hyperframes-mcp", "--label", "Move l-lift-1", "--json",
    ]  # fmt: skip
    assert fake.calls[1].argv == ["history", "end", "--json"]
    assert fake.calls[0].cwd == project


def test_an_undo_that_finds_a_file_changed_since_is_an_answer_with_the_conflict(monkeypatch, project, node_dir):
    conflict = {"ok": False, "conflict": {"files": ["index.html"], "newer": ["e2"]}}
    use(
        monkeypatch,
        prints_json({"ok": True, "entry": ENTRY}),
        prints_json(conflict, 2),
    )

    done = hf_cli.history_undo(project, "hyperframes-mcp", node_dir=node_dir)
    refused = hf_cli.history_undo(project, "hyperframes-mcp", node_dir=node_dir)

    assert done["ok"] is True and refused["conflict"]["newer"] == ["e2"]


def test_an_undo_with_nothing_of_ours_raises_with_the_clis_words(monkeypatch, project, node_dir):
    fake = use(
        monkeypatch,
        prints_json({"ok": False, "error": "hyperframes-mcp has no entry still in effect"}, 2),
    )

    with pytest.raises(hf_cli.HyperframesError, match="no entry still in effect"):
        hf_cli.history_undo(project, "hyperframes-mcp", node_dir=node_dir)
    assert fake.calls[0].argv == ["history", "undo", "--who", "hyperframes-mcp", "--json"]


def test_the_history_lists_the_newest_entries(monkeypatch, project, node_dir):
    fake = use(monkeypatch, prints_json({"entries": [ENTRY]}))

    assert hf_cli.history_list(project, limit=50, node_dir=node_dir) == [ENTRY]
    assert fake.calls[0].argv == ["history", "--json", "--limit", "50"]


# -- check and lint ----------------------------------------------------------------------------------


def test_check_samples_the_times_given_and_says_nothing_of_them_otherwise(monkeypatch, project, node_dir):
    fake = use(monkeypatch, prints_json({"ok": True}), prints_json({"ok": True}))

    hf_cli.check(project, [1.5, 7, 479.725], node_dir=node_dir)
    hf_cli.check(project, node_dir=node_dir)

    assert fake.calls[0].argv == ["check", "--json", "--at", "1.5,7,479.725"]
    assert fake.calls[1].argv == ["check", "--json"]
    assert fake.calls[0].cwd == project


def test_check_and_lint_exit_1_with_findings_is_an_answer_not_a_failure(monkeypatch, project, node_dir):
    finding = {"code": "duplicate_id", "severity": "error"}
    use(
        monkeypatch,
        prints_json({"ok": False, "runtime": {"errorCount": 1, "findings": [finding]}}, 1),
        prints_json({"ok": False, "errorCount": 1, "findings": [finding]}, 1),
    )

    checked = hf_cli.check(project, [1], node_dir=node_dir)
    linted = hf_cli.lint(project, node_dir=node_dir)

    assert checked["ok"] is False and checked["runtime"]["findings"] == [finding]
    assert linted["errorCount"] == 1 and "_meta" not in linted


def test_a_lint_that_crashes_is_a_failure(monkeypatch, project, node_dir):
    use(monkeypatch, printed(1, "", "TypeError: cannot read index.html"))

    with pytest.raises(hf_cli.HyperframesError, match="cannot read index.html"):
        hf_cli.lint(project, node_dir=node_dir)


# -- snapshot ----------------------------------------------------------------------------------------


def test_snapshot_returns_the_frames_the_cli_printed_in_order(monkeypatch, project, node_dir, tmp_path):
    out = tmp_path / "frames"
    listing = (
        "◆  Capturing 2 frames at [1s, 7.5s] from project\n\n"
        f"◇  2 snapshots saved to {out}\n"
        f"   {out}/frame-00-at-1s.png\n"
        f"   {out}/frame-01-at-7.5s.png\n"
        "   contact-sheet.jpg (grid view for AI review)\n"
    )
    fake = use(monkeypatch, printed(0, listing))

    frames = hf_cli.snapshot(project, [1, 7.5], out, node_dir=node_dir)

    assert frames == [out / "frame-00-at-1s.png", out / "frame-01-at-7.5s.png"]
    (call,) = fake.calls
    assert call.cwd == project
    # the exact times only, no outside vision model, an absolute folder
    assert call.argv == [
        "snapshot", "--at", "1,7.5", "--no-end", "--describe", "false", "-o", str(out),
    ]  # fmt: skip


def test_snapshot_that_fails_or_prints_no_frame_raises(monkeypatch, project, node_dir, tmp_path):
    use(
        monkeypatch,
        printed(1, "", "Error: could not launch the browser"),
        printed(0, "nothing captured\n"),
    )

    with pytest.raises(hf_cli.HyperframesError, match="could not launch the browser"):
        hf_cli.snapshot(project, [1], tmp_path / "f", node_dir=node_dir)
    with pytest.raises(hf_cli.HyperframesError, match="printed no frame"):
        hf_cli.snapshot(project, [1], tmp_path / "f", node_dir=node_dir)
    with pytest.raises(ValueError, match="at least one time"):
        hf_cli.snapshot(project, [], tmp_path / "f", node_dir=node_dir)


# -- a failure that says what is missing -------------------------------------------------------------


def test_a_failure_where_hyperframes_is_not_installed_says_so_and_how_to_install_it(
    node_dir,
):
    failed = hf_cli.HyperframesError("hyperframes snapshot failed: Node.js v24.21.0")

    said = hf_cli.explained(failed, node_dir)

    assert f"HyperFrames is not installed in {node_dir}" in said.reason
    assert "npm ci" in said.fix


def test_a_failure_of_an_installed_cli_keeps_its_reason_and_adds_the_browser_fix_it_lacks(
    node_dir,
):
    package = cli_script(node_dir).parents[1] / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "0.8.139"}))

    plain = hf_cli.explained(hf_cli.HyperframesError("no Chrome found"), node_dir)
    own = hf_cli.explained(hf_cli.HyperframesError("no", "do this"), node_dir)

    assert plain.reason == "no Chrome found" and "browser ensure" in plain.fix
    assert (own.reason, own.fix) == ("no", "do this")


# -- open --------------------------------------------------------------------------------------------


def test_open_hands_the_project_by_absolute_path_and_says_nothing_on_success(monkeypatch, project, node_dir):
    fake = use(monkeypatch, printed(0, json.dumps({"opened": True})))

    assert hf_cli.open_project(project, node_dir) is None

    (call,) = fake.calls
    assert call.argv == ["open", str(project), "--json"] and call.cwd == project


def test_open_without_the_app_says_where_to_get_it(monkeypatch, project, node_dir):
    answer = {
        "opened": False,
        "reason": "not-installed",
        "downloadUrl": "https://hyperframes.dev/download",
    }
    use(monkeypatch, printed(1, json.dumps(answer)))

    with pytest.raises(hf_cli.HyperframesError) as caught:
        hf_cli.open_project(project, node_dir)

    assert str(caught.value) == "not-installed; get the app at https://hyperframes.dev/download"


def test_open_says_the_cli_error_or_its_last_line(monkeypatch, project, node_dir):
    refused = {
        "ok": False,
        "error": {"code": "invalid-project", "message": "no index.html"},
    }
    use(
        monkeypatch,
        printed(1, json.dumps(refused)),
        printed(1, "", "boom\nnode crashed"),
        printed(1),
    )

    for why in ("no index.html", "node crashed", "no message"):
        with pytest.raises(hf_cli.HyperframesError) as caught:
            hf_cli.open_project(project, node_dir)
        assert str(caught.value) == why
