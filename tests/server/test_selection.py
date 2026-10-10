"""The ``<app_context>`` block of the HyperFrames app's messages, read into what the person pointed at, and what is on
screen there.

Fixtures: ``REAL_*`` are blocks cut from the app chat's own transcript (demo, 2026-10-06/07); they hold only times and
the app's words. The transcript has no pick, range or drawing, so the others were made by running the app's own
``pickedParts`` (``main/agentTargetLine.mjs``) on sample requests, and are written out as it printed them.
"""

from __future__ import annotations

import json

import pytest

from hyperframes_mcp.motion import manifest
from hyperframes_mcp.server import selection
from hyperframes_mcp.server.selection import (
    SelectionError,
    context_from_snapshot,
    parse_app_context,
    resolve,
)
from tests.motion.test_project_edit import composed, two_shots

# the app adds this after the target line whenever a target is named
OVERLAY = (
    "Overlay state (optional):\n"
    '- Print `<!-- hf:overlay {"kind":"editing","scope":"text","label":"Rewriting the headline"} -->` at any point '
    "and the app shows it on the selection in the preview.\n"
    "- kind: working | reading | thinking | editing. Without one, the selection just says the run is working.\n"
    "- scope: text | box | motion | content, what the edit is about; it selects a finer treatment.\n"
    "- label: one short line for the badge. accent: any CSS colour, if this run wants its own.\n"
    "- target: {selector} or {id} to point the state at a different element than the one asked about; leave it out "
    "for a timeline range.\n"
    "- Do not declare done or failed: the app sets those when the run exits.\n"
)


def message(words: str, block: str) -> str:
    """A message as the app sends it: the person's words, then the block."""
    return f"{words}\n\n<app_context>\n{block}\n</app_context>"


REAL_PLAYHEAD = message("go", "When the person sent this, the video's playhead was at 61.62s (01:01.6).")
REAL_PLAYHEAD_AT_START = message("ok", "When the person sent this, the video's playhead was at 0s (00:00).")
REAL_WITH_EARLIER_TURNS = message(
    "our timeline is messy can u clean it up?",
    "Earlier in this conversation, with Claude Code (for context; the person may refer to it):\n"
    "Person: can u load our 8 minute video there?\n\n"
    "Claude Code: Composing the full 8-minute video with the new labels and rows, then handing it to the desktop app.\n\n"
    "Claude Code: The 8-minute video is open in the app, ready for you. In the background, four reviewers are "
    "checking the last commit. They only read code and won't touch your project.\n\n"
    "When the person sent this, the video's playhead was at 333.36s (05:33.4).",
)
PICK = message(
    "make this one bigger",
    'Apply the instruction to the element at selector "#l-lift-1", tag "div", labelled "lift · Photo: lift-cage-mcp". '
    "The person clicked it at (960, 540) in composition pixels. The person picked it at 71.4s (01:11.4) in the "
    "video: that is the frame they were looking at. When the person sent this, the video's playhead was at 72.04s "
    f"(01:12).\n\n{OVERLAY}",
)
RANGE = message(
    "tighten this",
    "Apply the instruction only to the timeline range 61.2s to 70.4s, on timeline tracks 2, 3 (counting rows from "
    "the top, starting at 1): change what is there, and add content only where that stretch is empty. When the "
    f"person sent this, the video's playhead was at 70.4s (01:10.4).\n\n{OVERLAY}",
)
MOMENT = message(
    "what is here?",
    f"Apply the instruction to the video at the moment 12.5s.\n\n{OVERLAY}",
)
DRAWN = message(
    "fix this",
    "The person drew on the frame, 1 stroke as one drawing: at 33.7s (00:33.7) in the video; circled the area "
    "x 100-400, y 200-330 in composition pixels of the 1920x1080 frame (x 5-21%, y 19-31% of the frame), centred "
    "at (250, 265) [13% across, 25% down, top left]; the app found no element whose middle is inside it",
)
GESTURES = message(
    "1. move it up 2. point the arrow at the vault",
    "When the person sent this, the video's playhead was at 9.2s (00:09.2).\n\n"
    "The person made 2 gestures, numbered; a line of the instruction starting with a number is about that gesture:\n"
    '1. "the credit"; at 8.4s (00:08.4) in the video; circled the area x 1500-1900, y 960-1060 in composition '
    "pixels of the 1920x1080 frame (x 78-99%, y 89-98% of the frame), centred at (1700, 1010) [89% across, 94% "
    'down, bottom right]; around selector "#l-p1-2", labelled "p1 · Credit: US Army"\n'
    "2. at 9s (00:09) in the video; drew an arrow from (300, 300) [16% across, 28% down, top left] to (800, 500) "
    "[42% across, 46% down, centre] in composition pixels of the 1920x1080 frame, pointing right\n"
    "Each numbered mark is a place on the frame at its moment.",
)
SEVERAL = message(
    "make 2 bigger than 1",
    "Apply the instruction to these 2 elements, numbered as the person numbered them (an instruction such as "
    '"make 2 bigger than 1" uses these numbers): 1. selector "#l-a-1", labelled "a · Photo: one"; 2. selector '
    '"#l-b-1", labelled "b · Photo: two". The person picked them at 20s (00:20) in the video: that is the frame '
    "they were looking at. When the person sent this, the video's playhead was at 21s (00:21).\n\n" + OVERLAY,
)


# blocks for the project below
PICK_AT_HALF_A_SECOND = message(
    "this one",
    'Apply the instruction to the element at selector "#l-p1-1", tag "div". The person picked it at 0.5s (00:00.5) '
    "in the video: that is the frame they were looking at.",
)

AT_NINE = "When the person sent this, the video's playhead was at 9s (00:09)."

AT_EIGHT = "When the person sent this, the video's playhead was at 8s (00:08)."

WHOLE_VIDEO = "Apply the instruction only to the timeline range 0s to 100s: change what is there."


# ----- the four formats


def test_the_playhead_alone_is_where_the_person_was_looking():
    context = parse_app_context(REAL_PLAYHEAD)

    assert context.kind == "playhead"
    assert context.window() == (61.62, 61.62)
    assert context.selectors == () and context.marks == ()


def test_a_playhead_at_zero_is_a_time_not_an_absence():
    assert parse_app_context(REAL_PLAYHEAD_AT_START).window() == (0.0, 0.0)


def test_earlier_turns_the_app_hands_a_fresh_session_do_not_hide_the_playhead():
    context = parse_app_context(REAL_WITH_EARLIER_TURNS)

    assert context.window() == (333.36, 333.36)


def test_a_picked_element_names_its_selector_label_and_the_moment_it_was_picked_at():
    context = parse_app_context(PICK)

    assert context.kind == "pick"
    assert context.selectors == ("#l-lift-1",)
    assert context.labels == ("lift · Photo: lift-cage-mcp",)
    # the frame the person looked at, not where the playhead stood when they sent the message
    assert context.window() == (71.4, 71.4)
    assert context.playhead == 72.04


def test_several_picked_elements_are_all_named():
    context = parse_app_context(SEVERAL)

    assert context.selectors == ("#l-a-1", "#l-b-1")
    assert context.window() == (20.0, 20.0)


def test_a_timeline_range_is_the_stretch_and_the_rows_it_crossed():
    context = parse_app_context(RANGE)

    assert context.kind == "range"
    assert context.window() == (61.2, 70.4)
    assert context.tracks == (2, 3)


def test_a_moment_on_the_timeline_is_a_point():
    context = parse_app_context(MOMENT)

    assert context.kind == "playhead" and context.window() == (12.5, 12.5)


def test_a_drawn_mark_is_kept_in_the_apps_words_with_the_moment_it_was_drawn_at():
    context = parse_app_context(DRAWN)

    assert context.kind == "marks"
    assert context.window() == (33.7, 33.7)
    assert context.marks[0].startswith("circled the area x 100-400, y 200-330")
    assert len(context.marks[0]) <= 160


def test_numbered_gestures_give_each_mark_and_the_stretch_between_their_moments():
    context = parse_app_context(GESTURES)

    assert context.kind == "marks"
    assert [m.split(" ")[0] for m in context.marks] == ["circled", "drew"]
    assert context.selectors == ("#l-p1-2",)
    assert context.window() == (8.4, 9.0)
    assert context.playhead == 9.2


def test_a_selector_with_quotes_in_it_is_read_back_as_the_app_quoted_it():
    selector = '[data-hf-id="a\\b"]'
    block = f'Apply the instruction to the element at selector {json.dumps(selector)}, tag "div".'

    assert parse_app_context(block + " playhead was at 3s (00:03).").selectors == (selector,)


def test_a_block_with_no_time_is_refused_in_words():
    with pytest.raises(SelectionError, match="no time"):
        parse_app_context(message("hello", "Nothing to see here."))


def test_the_block_alone_or_the_whole_message_both_work():
    inner = "When the person sent this, the video's playhead was at 4.5s (00:04.5)."

    assert parse_app_context(inner).window() == (4.5, 4.5)
    assert parse_app_context(message("go", inner)).window() == (4.5, 4.5)


# ----- what is on screen there


@pytest.fixture
def project(tmp_path):
    """Shot p1 (a photo and its credit, 0 to 8 s) then shot m1 (a clip and its credit, 8 to 16 s); narration "It is
    April." at 0.2 to 1 s and "Gold." at 30 s."""
    root = composed(tmp_path, two_shots())[0]
    words = tmp_path / "words.json"
    words.write_text(
        json.dumps(
            [
                {"word": "It", "start": 0.2, "end": 0.4},
                {"word": "is", "start": 0.4, "end": 0.6},
                {"word": "April.", "start": 0.6, "end": 1.0},
                {"word": "Gold.", "start": 30.0, "end": 30.5},
            ]
        )
    )
    manifest.write_manifest(
        root,
        manifest.ProjectManifest(profile="history", topic_id=1, alignment=words),
    )
    return root


def test_a_moment_names_the_layers_on_screen_the_sentence_and_the_credits(project):
    found = resolve(project, parse_app_context(PICK_AT_HALF_A_SECOND))

    assert found["at"] == 0.5 and found["range"] is None
    assert [layer["id"] for layer in found["layers"]] == ["l-p1-1"]
    assert found["layers"][0]["file"] == "assets/media/photos/mine.jpg"
    assert found["layers"][0]["picked"] is True
    assert found["layers"][0]["credits"] == ["l-p1-2"]
    assert [c["id"] for c in found["credits"]] == ["l-p1-2"]
    assert "NARA" in found["credits"][0]["text"]
    assert found["shots"] == ["p1"]
    assert found["narration"] == "It is April." and found["speaking"] is True
    assert found["picked"] == [{"selector": "#l-p1-1", "layer": "l-p1-1"}]


def test_a_range_across_two_shots_names_both_and_whole_sentences(project):
    block = message(
        "tighten",
        "Apply the instruction only to the timeline range 0s to 20s, on timeline tracks 1, 2 (counting rows from "
        "the top, starting at 1): change what is there.",
    )

    found = resolve(project, parse_app_context(block))

    assert found["range"] == [0.0, 20.0] and found["at"] is None
    assert [layer["id"] for layer in found["layers"]] == ["l-p1-1", "l-m1-1"]
    assert found["shots"] == ["p1", "m1"]
    assert [c["id"] for c in found["credits"]] == ["l-p1-2", "l-m1-2"]
    assert found["narration"] == "It is April."
    assert found["tracks"] == [1, 2]


def test_a_moment_in_a_pause_gets_the_last_sentence_and_says_nobody_is_speaking(
    project,
):
    found = resolve(project, parse_app_context(message("hm", AT_NINE)))

    assert found["shots"] == ["m1"]
    assert [layer["id"] for layer in found["layers"]] == ["l-m1-1"]
    assert found["narration"] == "It is April." and found["speaking"] is False


def test_a_layer_that_starts_exactly_then_is_on_screen_and_the_one_that_ended_is_not(
    project,
):
    found = resolve(project, parse_app_context(message("", AT_EIGHT)))

    assert [layer["id"] for layer in found["layers"]] == ["l-m1-1"]


def test_the_apps_selection_snapshot_resolves_like_a_picked_element(project):
    snapshot = {
        "currentTime": 9.0,
        "target": {"id": "l-m1-1", "selector": "#l-m1-1"},
        "label": "m1 · Clip",
    }

    found = resolve(project, context_from_snapshot(snapshot))

    assert found["kind"] == "pick"
    assert found["picked"] == [{"selector": "#l-m1-1", "layer": "l-m1-1"}]
    assert found["layers"][0]["picked"] is True


def test_a_selection_inside_a_graphic_has_no_layer_id_but_still_gets_the_time(project):
    snapshot = {"currentTime": 1.0, "target": {"selector": "svg > text:nth-child(2)"}}

    found = resolve(project, context_from_snapshot(snapshot))

    assert found["picked"] == [{"selector": "svg > text:nth-child(2)", "layer": None}]
    assert [layer["id"] for layer in found["layers"]] == ["l-p1-1"]


def test_a_long_stretch_lists_only_the_most_layers_and_counts_them_all(project, monkeypatch):
    monkeypatch.setattr(selection, "MAX_LAYERS", 1)

    found = resolve(project, parse_app_context(message("all", WHOLE_VIDEO)))

    assert found["layers_total"] == 2 and len(found["layers"]) == 1


def test_a_layer_picked_on_the_timeline_is_given_with_its_own_time_credits_and_narration(
    project,
):
    # a timeline pick carries the playhead's moment (0.5 s, shot p1), but the clip picked is in shot m1 (8 to 16 s)
    block = message(
        "this clip",
        'Apply the instruction to these 2 elements, numbered as the person numbered them: 1. selector "#l-m1-1", '
        'labelled "m1 · Clip"; 2. selector "#l-p1-1", labelled "p1 · Photo". The person picked them at 0.5s (00:00.5) '
        "in the video: that is the frame they were looking at.",
    )

    found = resolve(project, parse_app_context(block))

    by_id = {layer["id"]: layer for layer in found["picked_layers"]}
    assert set(by_id) == {"l-m1-1", "l-p1-1"}
    clip = by_id["l-m1-1"]
    assert (clip["start"], clip["end"]) == (8.0, 16.0)
    assert clip["on_screen_at_pick"] is False
    assert clip["file"] and clip["credits"][0]["id"] == "l-m1-2"
    assert clip["credits"][0]["text"]
    photo = by_id["l-p1-1"]
    assert photo["on_screen_at_pick"] is True
    assert photo["narration"] == "It is April."
    # what is on screen at the moment is still described as before
    assert [layer["id"] for layer in found["layers"]] == ["l-p1-1"]


def test_a_pick_of_something_that_is_not_a_layer_gives_no_picked_layers(project):
    found = resolve(
        project,
        context_from_snapshot({"currentTime": 1.0, "target": {"selector": "svg > text"}}),
    )

    assert found["picked_layers"] == []
    assert found["picked"] == [{"selector": "svg > text", "layer": None}]
