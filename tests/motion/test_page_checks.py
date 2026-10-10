"""The checks of a composed page that the app's own turn check misses: a picture with no true credit, a credit hidden
behind the layer that covers the frame, and what the HyperFrames lint finds. The CLI is faked (``test_hf_cli`` has its
real shapes); the page is a composed project, edited the way the app edits it. The browser ``check`` is not run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion import hf_cli, page_checks
from tests.motion.test_project_edit import composed, two_shots

CLEAN_LINT = {"ok": True, "errorCount": 0, "warningCount": 0, "findings": []}


@pytest.fixture
def project(tmp_path) -> Path:
    return composed(tmp_path, two_shots())[0]


def edit(project: Path, change) -> str:
    """The page after ``change(page)``, written back."""
    path = project / "index.html"
    page = change(path.read_text(encoding="utf-8"))
    path.write_text(page, encoding="utf-8")
    return page


def remove_layer(layer_id: str):
    return lambda page: re.sub(rf'<div id="{layer_id}"[^>]*></div>\n?', "", page)


def set_z(layer_id: str, z: int):
    return lambda page: re.sub(rf'(id="{layer_id}".*?style="z-index: )\d+', rf"\g<1>{z}", page)


def fake_cli(monkeypatch, *, lint=None):
    """Stands in for the CLI; records its calls as ``("lint",)`` and fails on any other (no browser is ever run)."""
    calls: list[tuple] = []

    def run_lint(project, node_dir=None):
        calls.append(("lint",))
        if isinstance(lint, Exception):
            raise lint
        return lint or CLEAN_LINT

    def no_browser(*args, **kwargs):
        raise AssertionError("the browser check must not run")

    monkeypatch.setattr(hf_cli, "lint", run_lint)
    monkeypatch.setattr(hf_cli, "check", no_browser)
    monkeypatch.setattr(hf_cli, "version", lambda node_dir=None: "0.8.139")
    return calls


# -- the credits and the hidden layers of the page ---------------------------------------------------


def test_a_composed_page_is_clean(project):
    page = (project / "index.html").read_text(encoding="utf-8")

    assert page_checks.page_findings(page) == []


def test_a_picture_whose_credit_line_was_removed_is_an_error_at_the_pictures_start(
    project,
):
    page = edit(project, remove_layer("l-p1-2"))

    (found,) = page_checks.page_findings(page)

    assert (found.severity, found.layer, found.time) == ("error", "l-p1-1", 0.0)
    assert "no credit line" in found.what and "credit-line" in found.what


def test_a_credit_line_with_no_words_is_no_credit(project):
    page = edit(
        project,
        lambda page: page.replace("US Army Signal Corps, NARA 111-SC-204515", "   "),
    )

    found = page_checks.page_findings(page)

    # the picture has no credit, and the empty credit line itself would throw when the page is drawn
    assert [(f.severity, f.layer) for f in found] == [
        ("error", "l-p1-1"),
        ("error", "l-p1-2"),
    ]
    assert "would throw" in found[1].what


def test_a_credit_too_long_for_its_slot_is_an_error_naming_the_time_it_needs(project):
    # seen live: a twenty-word credit on a layer that fits six words; the block throws, nothing shows, a render fails
    long_credit = " ".join(["word"] * 25)
    page = edit(
        project,
        lambda page: page.replace("US Army Signal Corps, NARA 111-SC-204515", long_credit),
    )

    (found,) = page_checks.page_findings(page)

    assert (found.severity, found.layer) == ("error", "l-p1-2")
    assert "shorter than the 9.90 s credit-line needs" in found.what
    assert "give the layer at least 9.9 s or shorten the text" in found.what


def test_a_credit_line_put_under_the_picture_is_hidden_and_named_by_its_layer_ids(
    project,
):
    # the photo covers the frame; with the credit's z-index below it, the credit never shows
    page = edit(project, set_z("l-p1-2", 0))

    found = page_checks.page_findings(page)

    errors = [f for f in found if f.severity == "error"]
    assert [(f.layer, f.time) for f in errors] == [("l-p1-1", 0.0)]
    assert "its credit line (l-p1-2 (credit-line)) is hidden" in errors[0].what
    assert "put the credit line on a higher track" in errors[0].what
    assert "shot '" not in errors[0].what and "layer 1" not in errors[0].what
    # the credit layer itself is also reported as a layer that never shows
    assert any(f.severity == "warning" and f.layer == "l-p1-2" for f in found)


# -- what the CLI found -------------------------------------------------------------------------------

LAYOUT = {
    "code": "content_overlap",
    "severity": "error",
    "time": 2.0,
    "selector": "div.credit",
    "message": "Text overlaps other text.",
    "fixHint": "Move one of them.",
    "sourceFile": "compositions/credit-line.html",
}


def test_cli_findings_keep_errors_and_warnings_and_name_the_layer_on_screen(project):
    page = (project / "index.html").read_text(encoding="utf-8")
    answer = {
        "lint": {
            "findings": [
                {
                    "code": "host_missing_composition_id",
                    "severity": "error",
                    "message": "Host is missing an id.",
                    "elementId": "l-m1-1",
                    "fixHint": "Set it.",
                },
                {"code": "x", "severity": "info", "message": "not shown"},
            ]
        },
        "layout": {
            "findings": [
                LAYOUT,
                {**LAYOUT, "severity": "info", "code": "escaped_container"},
                {**LAYOUT, "time": 3.0},  # the same finding seen again
            ]
        },
        "contrast": {
            "findings": [
                {
                    "code": "contrast",
                    "severity": "warning",
                    "time": 9.0,
                    "message": "Low contrast.",
                    "sourceFile": "compositions/credit-line.html",
                }
            ]
        },
    }

    found = page_checks.cli_findings(answer, page_checks.layers_from_page(page))

    assert [(f.severity, f.layer, f.time) for f in found] == [
        ("error", "l-m1-1", 8.0),  # from its id: the layer's start
        ("error", "l-p1-2", 2.0),  # the credit line on screen at 2 s
        ("warning", "l-m1-2", 9.0),  # the credit line on screen at 9 s
    ]
    assert found[0].what == "host_missing_composition_id: Host is missing an id. Fix: Set it."
    assert found[1].what.endswith("Fix: Move one of them.")


def test_the_soft_plate_of_a_block_is_not_reported_as_escaping_its_container():
    # the plates behind a block's text are larger than their parent on purpose (seen on a real check of a date stamp)
    plate = {
        "code": "escaped_container",
        "severity": "warning",
        "time": 5,
        "selector": "div.scrim",
        "message": "Positioned element renders far outside its offset parent.",
        "sourceFile": "compositions/photo-shot.html",
    }
    other = {**plate, "selector": "div.card"}

    found = page_checks.cli_findings({"layout": {"findings": [plate, other]}}, [])

    assert len(found) == 1  # only the card's


def test_a_cli_finding_of_the_root_page_names_no_layer(project):
    page = (project / "index.html").read_text(encoding="utf-8")
    answer = {
        "lint": {
            "findings": [
                {
                    "code": "gsap_timeline_not_registered",
                    "severity": "error",
                    "message": "Not registered.",
                    "file": "/somewhere/index.html",
                }
            ]
        }
    }

    (found,) = page_checks.cli_findings(answer, page_checks.layers_from_page(page))

    assert (found.layer, found.time) == (None, None)


def test_a_long_finding_is_cut_to_a_sentence_or_two():
    long = {"code": "c", "severity": "error", "message": "word " * 200}

    (found,) = page_checks.cli_findings({"lint": {"findings": [long]}}, [])

    assert len(found.what) <= page_checks.MAX_WHAT and found.what.endswith("…")


# -- the whole run -----------------------------------------------------------------------------------


def test_a_clean_project_gets_one_ok_line_and_only_the_lint_runs(project, monkeypatch):
    calls = fake_cli(monkeypatch)

    done = page_checks.run_checks(project)

    assert done["ok"] is True and done["findings"] == []
    assert done["summary"] == "ok: no problems in 4 layers"
    assert calls == [("lint",)]


def test_findings_from_the_page_and_the_lint_come_errors_first_then_by_time(project, monkeypatch):
    edit(project, remove_layer("l-m1-2"))
    fake_cli(
        monkeypatch,
        lint={
            "ok": True,
            "findings": [
                {
                    "code": "slow",
                    "severity": "warning",
                    "message": "Too slow.",
                    "elementId": "l-p1-2",
                }
            ],
        },
    )

    done = page_checks.run_checks(project)

    assert done["ok"] is False
    assert [(f["severity"], f["layer"]) for f in done["findings"]] == [
        ("error", "l-m1-1"),
        ("warning", "l-p1-2"),
    ]
    assert done["summary"].startswith("1 error, 1 warning")


def test_lint_errors_make_the_run_not_ok_and_the_browser_is_never_asked(project, monkeypatch):
    fake_cli(
        monkeypatch,
        lint={"findings": [{"code": "bad", "severity": "error", "message": "Broken."}]},
    )

    done = page_checks.run_checks(project)

    assert done["ok"] is False and len(done["findings"]) == 1
    assert done["not_run"] == []


def test_a_hyperframes_that_is_not_installed_is_said_so_with_how_to_install_it(project, monkeypatch):
    fake_cli(monkeypatch, lint=hf_cli.HyperframesError("hyperframes lint failed: x"))
    monkeypatch.setattr(hf_cli, "version", lambda node_dir=None: None)

    done = page_checks.run_checks(project)

    (note,) = done["not_run"]
    assert note.startswith("lint not run:") and "npm ci" in note


def test_a_long_list_is_cut_to_the_first_findings_with_the_count_left(project, monkeypatch):
    many = [{"code": f"c{n}", "severity": "error", "message": f"Problem {n}."} for n in range(60)]
    fake_cli(monkeypatch, lint={"findings": many})

    done = page_checks.run_checks(project)

    assert len(done["findings"]) == page_checks.MAX_FINDINGS
    assert done["more"] == 60 - page_checks.MAX_FINDINGS
    assert "60 errors" in done["summary"]
    assert "more not shown" in done["summary"]
