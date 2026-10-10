"""Timeline edits by our layer ids: the ops checked before the CLI sees them, what a plan changes, the ids a split or a
duplicate gets, and the page finished after HyperFrames wrote it. The CLI is not run here: its answers are built by hand
in the shape ``tests/motion/test_hf_cli.py`` has from the real one."""

from __future__ import annotations

import json
import re

import pytest

from hyperframes_mcp.motion import project_edit, timeline_edit
from hyperframes_mcp.motion.project_edit import EditError, layers_from_page
from hyperframes_mcp.motion.timeline_edit import Made
from tests.motion.test_hf_cli import row
from tests.motion.test_project_edit import composed, two_shots

# the project of tests/motion/test_project_edit: a photo 0 to 8 s (l-p1-1) with a credit 0.3 to 8 (l-p1-2), a clip 8 to 16
# (l-m1-1) with a credit 8.3 to 16 (l-m1-2); the voice makes it 20 s long
LENGTH = 20.0


@pytest.fixture
def page(tmp_path) -> str:
    project = composed(tmp_path, two_shots())[0]
    return (project / "index.html").read_text(encoding="utf-8")


# ----- the ops


@pytest.mark.parametrize(
    ("op", "words"),
    [
        (
            {"verb": "swap", "layer": "l-p1-1"},
            "verb must be one of move, trim, split, duplicate, delete",
        ),
        ({"verb": "move", "layer": "l-p1-1", "ref": "#x"}, "unknown field ref"),
        ({"verb": "move", "layer": "l-x9-1", "time": 1}, "no layer 'l-x9-1'"),
        (
            {"verb": "move", "layer": "l-p1-9", "time": 1},
            "the layers of shot 'p1' are l-p1-1, l-p1-2",
        ),
        ({"verb": "move", "layer": "l-p1-1"}, "needs time"),
        ({"verb": "trim", "layer": "l-p1-2"}, "needs start or end or duration"),
        (
            {"verb": "trim", "layer": "l-p1-2", "duration": -1},
            "duration must be above 0",
        ),
        (
            {"verb": "trim", "layer": "l-p1-2", "duration": "-1"},
            "duration must be above 0",
        ),
        (
            {"verb": "trim", "layer": "l-p1-2", "duration": 0},
            "duration must be above 0",
        ),
        (
            {"verb": "trim", "layer": "l-p1-2", "start": 5, "end": 3},
            "must end after it starts",
        ),
        (
            {"verb": "trim", "layer": "l-m1-2", "end": 25},
            r"25 s is past the end of the video \(20 s long\)",
        ),
        ({"verb": "split", "layer": "l-p1-1", "time": 99}, "past the end of the video"),
        (
            {"verb": "move", "layer": "l-p1-1", "time": -1},
            "before the start of the video",
        ),
        ({"verb": "move", "layer": "l-p1-1", "time": True}, "must be seconds"),
        ({"verb": "move", "layer": "l-p1-1", "time": "soon"}, "is not seconds"),
        (
            {"verb": "move", "layer": "l-p1-1", "time": "after:l-zz-1"},
            "no layer 'l-zz-1'",
        ),
    ],
)
def test_an_op_that_cannot_be_right_is_refused_in_words_before_the_cli_sees_it(page, op, words):
    with pytest.raises(EditError, match=words):
        timeline_edit.cli_ops(page, [{"verb": "delete", "layer": "l-m1-2"}, op], LENGTH)


def test_a_failed_op_is_named_by_its_number(page):
    with pytest.raises(EditError, match="^op 2 "):
        timeline_edit.cli_ops(
            page,
            [
                {"verb": "delete", "layer": "l-m1-2"},
                {"verb": "move", "layer": "l-p1-1"},
            ],
            LENGTH,
        )


def test_good_ops_become_the_clis_ops_on_our_ids_with_times_as_text(page):
    ops = [
        {"verb": "move", "layer": "l-p1-2", "time": 2.5},
        {"verb": "move", "layer": "l-p1-2", "time": "+1"},
        {"verb": "trim", "layer": "l-m1-2", "start": "8.5", "end": 12},
        {"verb": "split", "layer": "l-p1-1", "time": 4},
        {"verb": "duplicate", "layer": "l-p1-2", "at": "after:l-m1-2"},
        {"verb": "delete", "layer": "l-m1-2"},
    ]

    assert timeline_edit.cli_ops(page, ops, LENGTH) == [
        {"verb": "move", "ref": "#l-p1-2", "time": "2.5"},
        {"verb": "move", "ref": "#l-p1-2", "time": "+1"},
        {"verb": "trim", "ref": "#l-m1-2", "start": "8.5", "end": "12"},
        {"verb": "split", "ref": "#l-p1-1", "time": "4"},
        {"verb": "duplicate", "ref": "#l-p1-2", "at": "after:#l-m1-2"},
        {"verb": "delete", "ref": "#l-m1-2"},
    ]


# ----- the ids of what a split or a duplicate makes

BLOCKS = {"l-p1-1": "photo-shot", "l-p1-2": "credit-line", "l-m1-1": "video-shot"}


def test_a_new_layer_takes_the_next_free_number_of_the_shot_of_the_layer_it_was_made_from():
    made = timeline_edit.new_ids(BLOCKS, ["l-p1-1-2", "l-p1-2-copy", "l-m1-1-2"])

    assert made == {
        "l-p1-1-2": Made("l-p1-3", "l-p1-1", "photo-shot"),
        "l-p1-2-copy": Made("l-p1-4", "l-p1-2", "credit-line"),
        "l-m1-1-2": Made("l-m1-2", "l-m1-1", "video-shot"),
    }


def test_a_copy_of_a_copy_is_tied_to_the_layer_it_started_from():
    made = timeline_edit.new_ids(BLOCKS, ["l-p1-2-copy", "l-p1-2-copy-2"])

    assert [m.id for m in made.values()] == ["l-p1-3", "l-p1-4"]
    assert {m.origin for m in made.values()} == {"l-p1-2"}


def test_a_layer_that_cannot_be_tied_to_one_of_the_page_is_an_error():
    with pytest.raises(EditError, match="not tied to a layer of the page"):
        timeline_edit.new_ids(BLOCKS, ["l-zz-9-2"])


# ----- what a plan changes


def summary_of(page, ops, before, after, length=LENGTH):
    return timeline_edit.summarise(page, ops, {"before": before, "after": after}, length)


def test_a_split_and_a_trim_are_each_layer_with_its_old_and_new_time(page):
    ops = [
        {"verb": "split", "ref": "#l-p1-1", "time": "4"},
        {"verb": "trim", "ref": "#l-m1-2", "end": "14"},
    ]

    summary = summary_of(
        page,
        ops,
        [row("l-p1-1", 0, 8), row("l-m1-2", 8.3, 16)],
        [row("l-p1-1", 0, 4), row("l-p1-1-2", 4, 8), row("l-m1-2", 8.3, 14)],
    )

    assert summary.changes == [
        {"layer": "l-p1-1", "was": [0, 8], "now": [0, 4]},
        {"layer": "l-p1-3", "was": None, "now": [4, 8], "made_from": "l-p1-1"},
        {"layer": "l-m1-2", "was": [8.3, 16], "now": [8.3, 14]},
    ]
    assert summary.problems == []
    assert {i: layer.values["durationSeconds"] for i, layer in summary.retimed.items()} == {
        "l-p1-1": 4.0,
        "l-p1-3": 4.0,
        "l-m1-2": 5.7,
    }
    assert summary.check_at() == [0.5, 4.5, 8.8]


def test_a_move_is_not_a_new_length_and_a_removed_layer_is_listed_with_where_it_was(
    page,
):
    ops = [
        {"verb": "move", "ref": "#l-p1-2", "time": "2"},
        {"verb": "delete", "ref": "#l-m1-2"},
    ]

    summary = summary_of(
        page,
        ops,
        [row("l-p1-2", 0.3, 8), row("l-m1-2", 8.3, 16)],
        [row("l-p1-2", 2, 9.7)],
    )

    assert summary.changes == [
        {"layer": "l-p1-2", "was": [0.3, 8], "now": [2, 9.7]},
        {"layer": "l-m1-2", "was": [8.3, 16], "now": None},
    ]
    assert summary.retimed == {}
    assert summary.check_at() == [2.5, 8.3]


def test_a_layer_the_edit_pushes_on_is_listed_and_one_pushed_past_the_end_refuses_the_plan(
    page,
):
    ops = [{"verb": "duplicate", "ref": "#l-p1-2", "at": "8"}]

    summary = summary_of(
        page,
        ops,
        [row("l-p1-2", 0.3, 8), row("l-m1-2", 8.3, 16)],
        [row("l-p1-2", 0.3, 8), row("l-p1-2-copy", 8, 15.7), row("l-m1-2", 15.7, 23.4)],
    )

    by_layer = {c["layer"]: c for c in summary.changes}
    assert by_layer["l-p1-2"]["unchanged"] is True
    assert by_layer["l-p1-3"]["made_from"] == "l-p1-2"
    assert by_layer["l-m1-2"]["pushed"] is True and by_layer["l-m1-2"]["now"] == [
        15.7,
        23.4,
    ]
    assert summary.problems == [
        "l-m1-2 would end at 23.4 s, after the end of the video (20 s); shorten it, or move what pushes it"
    ]


@pytest.mark.parametrize(
    ("end", "words"),
    [
        (
            1.0,
            r"l-p1-2 \(credit-line\): a slot of 0.7 s is shorter than the block's minimum of 2.4 s",
        ),
        (15.3, r"a slot of 15 s is longer than the block's maximum of 14 s"),
    ],
)
def test_a_slot_outside_its_blocks_range_refuses_the_plan(page, end, words):
    ops = [{"verb": "trim", "ref": "#l-p1-2", "end": str(end)}]
    start = 0.3

    summary = summary_of(page, ops, [row("l-p1-2", start, 8)], [row("l-p1-2", start, end)])

    (problem,) = summary.problems
    assert re.search(words, problem)


def test_the_clis_own_warnings_are_kept(page):
    ops = [{"verb": "move", "ref": "#l-p1-2", "time": "2"}]

    summary = timeline_edit.summarise(
        page,
        ops,
        {
            "before": [row("l-p1-2", 0.3, 8)],
            "after": [row("l-p1-2", 2, 9.7)],
            "warnings": ["l-p1-2 now overlaps a credit"],
        },
        LENGTH,
    )

    assert "l-p1-2 now overlaps a credit" in summary.warnings


# ----- the page after HyperFrames wrote it


def written_by_the_cli(page: str) -> str:
    """The page as ``timeline split l-p1-1 4`` leaves it: the first half shorter, a second half under the CLI's id and
    with its block named ``photo-shot-split`` (read from the real CLI's output)."""
    tag = project_edit.find_tag(page, "l-p1-1")
    first = project_edit.set_attributes(tag, tag, {"data-duration": "4"})
    second = project_edit.set_attributes(
        tag,
        tag,
        {
            "id": "l-p1-1-2",
            "data-composition-id": "photo-shot-split",
            "data-start": "4",
            "data-duration": "4",
        },
    )
    return page.replace(tag, first + "\n      " + second)


def test_a_split_layer_gets_our_id_its_blocks_name_and_every_retimed_layer_its_new_length(
    page,
):
    written = written_by_the_cli(page)
    summary = summary_of(
        page,
        [{"verb": "split", "ref": "#l-p1-1", "time": "4"}],
        [row("l-p1-1", 0, 8)],
        [row("l-p1-1", 0, 4), row("l-p1-1-2", 4, 8)],
    )

    finished = timeline_edit.finish_page(written, summary)

    layers = {f"l-{l.shot}-{l.index}": l for l in layers_from_page(finished)}
    assert sorted(layers) == ["l-m1-1", "l-m1-2", "l-p1-1", "l-p1-2", "l-p1-3"]
    second = layers["l-p1-3"]
    assert (second.component, second.start, second.end) == ("photo-shot", 4, 8)
    assert [layers[i].values["durationSeconds"] for i in ("l-p1-1", "l-p1-3")] == [
        4.0,
        4.0,
    ]
    assert 'data-composition-id="photo-shot-split"' not in finished
    # the same image, and the first half's other values, are in the second half
    assert second.values["image"] == layers["l-p1-1"].values["image"]


def test_finishing_a_page_that_is_finished_already_changes_nothing(page):
    summary = summary_of(
        page,
        [{"verb": "split", "ref": "#l-p1-1", "time": "4"}],
        [row("l-p1-1", 0, 8)],
        [row("l-p1-1", 0, 4), row("l-p1-1-2", 4, 8)],
    )
    once = timeline_edit.finish_page(written_by_the_cli(page), summary)

    assert timeline_edit.finish_page(once, summary) == once


def test_the_variables_of_a_layer_are_json_in_the_page_as_before(page):
    summary = summary_of(
        page,
        [{"verb": "trim", "ref": "#l-m1-2", "end": "14"}],
        [row("l-m1-2", 8.3, 16)],
        [row("l-m1-2", 8.3, 14)],
    )
    tag = project_edit.find_tag(page, "l-m1-2")
    trimmed = page.replace(tag, project_edit.set_attributes(tag, tag, {"data-duration": "5.7"}))

    finished = timeline_edit.finish_page(trimmed, summary)

    mount = project_edit.mounts(finished)["l-m1-2"]
    assert mount.layer.values["durationSeconds"] == 5.7
    assert json.loads(json.dumps(mount.layer.values)) == mount.layer.values
