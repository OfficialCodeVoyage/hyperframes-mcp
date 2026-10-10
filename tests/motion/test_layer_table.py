"""The table of a project's layers: when each is on screen, what it is, who is credited with it, and what is said."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

import pytest

from hyperframes_mcp.motion import layer_table, project_edit
from hyperframes_mcp.motion.compose import compose
from hyperframes_mcp.motion.layer_table import CreditShown, Speech
from hyperframes_mcp.motion.layer_table import layer_table as table
from hyperframes_mcp.motion.manifest import ProjectManifest, write_manifest
from hyperframes_mcp.motion.preflight import credits_of
from hyperframes_mcp.motion.shotlist import parse_shotlist
from hyperframes_mcp.motion.timing import Word
from tests.motion.composer_helpers import (
    clip_layer,
    credit_layer,
    fake_registry,
    fake_shotlist,
    map_layer,
    photo_layer,
)

AI_CREDIT = "Engraving-style illustration, AI-generated"


def speak(*sentences: tuple[float, str], step: float = 0.4) -> list[Word]:
    """Words of the sentences, each sentence starting at its time, one word every ``step`` seconds."""
    words = []
    for at, text in sentences:
        for n, word in enumerate(text.split()):
            start = round(at + n * step, 3)
            words.append(Word(word, start, round(start + step * 0.8, 3)))
    return words


# A is said in p1; B starts in p1 and runs on into m1; C is in m1; D is in c1; E is in p2.
SPEECH = [
    (0.1, "It is April 1945."),
    (6.5, "You ride a lift down a shaft that falls away."),
    (11.0, "Then the map opens, and a route appears."),
    (17.0, "Nobody said a word!"),
    (24.0, "Money moved quietly?"),
]


@pytest.fixture
def project(tmp_path) -> Path:
    """p1 0-8 s a photo with a Signal Corps credit, m1 8-16 s a map, c1 16-22 s a clip with an AI credit, p2 22-28 s a
    photo whose file record says it is AI-made under a plain credit."""
    assets = tmp_path / "assets"
    for name in (
        "photos/mine.jpg",
        "photos/made.png",
        "clips/shot.mp4",
        "audio/voice.wav",
    ):
        (assets / name).parent.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(name.encode())
    raw = fake_shotlist()
    raw["shots"] = [
        {
            "id": "p1",
            "start": 0,
            "duration": 8,
            "layers": [photo_layer(), credit_layer()],
        },
        {"id": "m1", "start": 8, "duration": 8, "layers": [map_layer()]},
        {
            "id": "c1",
            "start": 16,
            "duration": 6,
            "layers": [clip_layer(), credit_layer(variables={"text": AI_CREDIT})],
        },
        {
            "id": "p2",
            "start": 22,
            "duration": 6,
            "layers": [
                photo_layer(variables={"image": "photos/made.png"}),
                credit_layer(variables={"text": "Studio, 1950"}),
            ],
        },
    ]
    out = tmp_path / "project"
    compose(
        parse_shotlist(raw),
        [],
        30.0,
        out,
        assets_dir=assets,
        registry=fake_registry(tmp_path / "reg"),
    )
    (out / "assets/media/photos/made.png.json").write_text(json.dumps({"ai": True}))
    return out


def by_id(rows) -> dict:
    return {row.id: row for row in rows}


def test_each_layer_is_a_row_in_time_order_with_its_page_facts(project):
    rows = table(project)

    assert [row.id for row in rows] == [
        "l-p1-1",
        "l-p1-2",
        "l-m1-1",
        "l-c1-1",
        "l-c1-2",
        "l-p2-1",
        "l-p2-2",
    ]
    photo = by_id(rows)["l-p1-1"]
    assert (photo.start, photo.end, photo.shot, photo.kind) == (
        0.0,
        8.0,
        "p1",
        "photo-shot",
    )
    assert photo.label == "p1 · Photo: mine"
    assert photo.file == "assets/media/photos/mine.jpg"
    assert photo.text is None
    credit = by_id(rows)["l-p1-2"]
    assert (credit.start, credit.end, credit.kind) == (0.3, 8.0, "credit-line")
    assert credit.text == "US Army Signal Corps, NARA 111-SC-204515"
    assert credit.file is None
    assert by_id(rows)["l-m1-1"].text == "Germany"


def test_a_picture_lists_the_credit_lines_on_screen_with_it_and_nothing_else_does(
    project,
):
    rows = by_id(table(project))

    assert rows["l-p1-1"].credits == [CreditShown("l-p1-2", "US Army Signal Corps, NARA 111-SC-204515")]
    assert rows["l-c1-1"].credits == [CreditShown("l-c1-2", AI_CREDIT)]
    assert rows["l-p1-2"].credits == []
    assert rows["l-m1-1"].credits == []


def test_a_layer_is_ai_archival_or_a_graphic(project):
    rows = by_id(table(project))

    assert rows["l-p1-1"].source == "archival"  # a picture with another credit
    assert rows["l-c1-1"].source == "ai"  # its credit says AI-generated
    assert rows["l-p2-1"].source == "ai"  # its file's record says ai, whatever the credit
    assert rows["l-m1-1"].source == "graphic"
    assert rows["l-p1-2"].source == "graphic"  # a credit line is a block, not a picture


def test_a_files_record_that_is_not_a_json_object_is_the_same_as_no_record(project):
    (project / "assets/media/photos/mine.jpg.json").write_text("not json")
    (project / "assets/media/photos/made.png.json").write_text("[1]")

    rows = by_id(table(project))

    assert rows["l-p1-1"].source == "archival"
    assert rows["l-p2-1"].source == "archival"


def test_the_narration_of_a_row_is_the_sentences_said_while_it_is_on_screen(project):
    rows = by_id(table(project, words=speak(*SPEECH)))

    assert rows["l-p1-1"].narration == ("It is April 1945. You ride a lift down a shaft that falls away.")
    # a sentence begun before the layer is said whole, and one that runs past its end too
    assert rows["l-m1-1"].narration == (
        "You ride a lift down a shaft that falls away. Then the map opens, and a route appears."
    )
    assert rows["l-c1-1"].narration == "Nobody said a word!"
    assert rows["l-c1-2"].narration == "Nobody said a word!"
    assert rows["l-p2-1"].narration == "Money moved quietly?"


def test_without_words_the_rows_have_no_narration(project):
    assert {row.narration for row in table(project)} == {""}
    assert {row.narration for row in table(project, words=[])} == {""}


def test_only_the_rows_that_overlap_the_window_are_listed(project):
    def ids(**window) -> list[str]:
        return [row.id for row in table(project, **window)]

    assert ids(from_s=9, to_s=10) == ["l-m1-1"]
    assert ids(from_s=7.9, to_s=8.1) == ["l-p1-1", "l-p1-2", "l-m1-1"]
    # a layer that ends where the window starts, or starts where it ends, is not on screen in it
    assert ids(from_s=8, to_s=8.5) == ["l-m1-1"]
    assert ids(to_s=16) == ["l-p1-1", "l-p1-2", "l-m1-1"]
    assert ids(from_s=22.3) == ["l-p2-1", "l-p2-2"]


def test_only_the_asked_kinds_are_listed(project):
    def ids(*kinds: str) -> list[str]:
        return [row.id for row in table(project, kinds=kinds)]

    assert ids("photo-shot") == ["l-p1-1", "l-p2-1"]
    assert ids("video-shot", "map-route") == ["l-m1-1", "l-c1-1"]
    assert ids("nonsense") == []


def test_the_table_reads_a_page_whose_classes_the_app_renamed(project):
    before = table(project)
    index = project / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace('class="clip shot-p1"', 'class="clip fade-hole-still"'),
        encoding="utf-8",
    )

    assert table(project) == before


# -- the words of the narration ---------------------------------------------------------------------


def test_the_speech_widens_a_stretch_to_whole_sentences():
    speech = Speech(speak((0, "One two. Three four five. Six seven.")))

    assert speech.between(0.5, 0.6) == "One two."
    assert speech.between(1.1, 1.2) == "Three four five."
    assert speech.between(0.5, 1.3) == "One two. Three four five."
    assert speech.between(2.7, 9) == "Six seven."
    assert speech.between(0, 99) == "One two. Three four five. Six seven."


def test_a_sentence_ends_at_a_full_stop_a_question_or_exclamation_mark_even_in_quotes():
    speech = Speech(speak((0, 'He said "stop." Why? Go! (Wait.) And on')))

    assert speech.between(0.8, 1.0) == 'He said "stop."'
    assert speech.between(1.2, 1.3) == "Why?"
    assert speech.between(1.6, 1.7) == "Go!"
    assert speech.between(2.0, 2.1) == "(Wait.)"
    # the last sentence needs no mark
    assert speech.between(2.9, 9) == "And on"


def test_a_stretch_with_no_words_in_it_has_no_narration():
    speech = Speech(speak((10, "Late words.")))

    assert speech.between(0, 5) == ""
    assert speech.between(20, 30) == ""
    assert Speech([]).between(0, 5) == ""


def test_a_word_begun_before_the_stretch_and_still_said_in_it_counts():
    speech = Speech([Word("Long", 0.0, 3.0), Word("word.", 3.1, 3.5), Word("Next.", 4, 4.5)])

    assert speech.between(2.5, 3.05) == "Long word."


def test_a_long_project_is_fast():
    words = [Word(f"w{n}." if n % 12 == 11 else f"w{n}", n * 0.3, n * 0.3 + 0.2) for n in range(1200)]
    began = time.perf_counter()

    speech = Speech(words)
    narration = [speech.between(n * 2.3, n * 2.3 + 2) for n in range(156)]

    assert all(narration)
    assert time.perf_counter() - began < 1


# -- the words of a project -------------------------------------------------------------------------


def alignment_file(project: Path) -> Path:
    path = project.parent / "words.json"
    path.write_text(json.dumps([{"word": w.word, "start": w.start, "end": w.end} for w in speak(*SPEECH)]))
    return path


def test_the_words_of_a_project_are_the_alignment_its_manifest_names(project):
    assert layer_table.project_words(project) is None  # no manifest
    write_manifest(project, ProjectManifest())
    assert layer_table.project_words(project) is None  # no alignment in it
    write_manifest(project, ProjectManifest(alignment=alignment_file(project)))

    words = layer_table.project_words(project)

    assert [w.word for w in words[:4]] == ["It", "is", "April", "1945."]


# -- the command ------------------------------------------------------------------------------------


def test_the_table_cuts_a_long_narration_with_an_ellipsis(project):
    rows = table(project, words=speak((0.0, " ".join(["word"] * 80) + ".")))

    lines = layer_table.format_table(rows).splitlines()

    assert lines[1].endswith("…") and "word word" in lines[1]
    assert len(lines[1]) < 260


# -- the full text, and whose credit a credit line is -----------------------------------------------

LONG_CREDIT = "Photo: U.S. Mint, San Francisco, c.1935, National Archives and Records Administration (public domain)"


def test_a_blocks_text_is_whole_and_only_the_printed_table_cuts_it(project):
    index = project / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace("US Army Signal Corps, NARA 111-SC-204515", LONG_CREDIT),
        encoding="utf-8",
    )

    rows = table(project)

    assert by_id(rows)["l-p1-2"].text == LONG_CREDIT
    assert by_id(rows)["l-p1-1"].credits == [CreditShown("l-p1-2", LONG_CREDIT)]
    printed = layer_table.format_table(rows)
    assert LONG_CREDIT not in printed
    assert "Photo: U.S. Mint, San Francisco" in printed


def page_of(*layers: tuple[str, int, str, float, float, dict]) -> str:
    """A page of mounts, each ``(shot, number, component, start, seconds, variables)``, stacked in the order given."""
    return "\n".join(
        f'<div id="l-{shot}-{n}" class="clip" data-composition-id="{component}" data-start="{start}" '
        f'data-duration="{seconds}" data-variable-values="{html.escape(json.dumps(values))}" '
        f'style="z-index: {z}"></div>'
        for z, (shot, n, component, start, seconds, values) in enumerate(layers, 1)
    )


def rows_of_page(tmp_path: Path, *layers: tuple) -> dict:
    (tmp_path / "index.html").write_text(page_of(*layers), encoding="utf-8")
    return by_id(table(tmp_path))


def photo(n: int, start: float, seconds: float) -> tuple:
    return ("s", n, "photo-shot", start, seconds, {"image": f"assets/media/{n}.jpg"})


def credit(n: int, start: float, seconds: float, text: str) -> tuple:
    return ("s", n, "credit-line", start, seconds, {"text": text})


def credited(rows: dict, layer_id: str) -> list[str]:
    return [c.id for c in rows[layer_id].credits]


def test_a_graphics_source_credit_is_its_own_and_not_the_credit_of_the_picture_it_overlaps(
    tmp_path,
):
    # "price-now" of the gold video: a vault photo with its credit, a counter and the source of its figure (which sits
    # over the vault photo in time), a second photo with its credit, and a date plate that starts just before that credit
    rows = rows_of_page(
        tmp_path,
        photo(1, 60.68, 5.4),
        credit(2, 60.83, 5.4, "Photo: U.S. Mint, San Francisco, c.1935, NARA"),
        photo(3, 66.08, 3.63),
        credit(4, 66.23, 4.5, "US Army Signal Corps, National Archives"),
        ("s", 5, "date-stamp", 66.18, 3.65, {"text": "Marburg, April 1945"}),
        credit(6, 60.88, 5.4, "Spot gold $4,156.30 an ounce, 5 October 2026 (CNBC)"),
        ("s", 7, "counter", 60.68, 7.6, {"label": "the lower figure", "to": 14}),
    )

    assert credited(rows, "l-s-1") == ["l-s-2"]
    assert credited(rows, "l-s-3") == ["l-s-4"]
    # the counter shows its source as its credit; the date plate has none
    assert credited(rows, "l-s-7") == ["l-s-6"]
    assert rows["l-s-7"].credits[0].text.startswith("Spot gold $4,156.30")
    assert rows["l-s-7"].source == "graphic"
    assert credited(rows, "l-s-5") == []
    # a credit line holds no credit of its own
    assert [credited(rows, f"l-s-{n}") for n in (2, 4, 6)] == [[], [], []]


def test_the_attribution_leaves_the_preflights_credit_rule_as_it_was(tmp_path):
    # the rule asks only for a credit line on screen with the picture, whichever layer that credit belongs to
    layers = [
        photo(1, 0, 5),
        credit(2, 0.15, 5, "Its own credit"),
        ("s", 3, "counter", 0, 7, {"label": "x"}),
        credit(4, 0.2, 5, "The counter's source"),
    ]
    found = [m.layer for m in project_edit.mounts(page_of(*layers)).values()]

    assert [c.index for c in credits_of(found[0], found)] == [2, 4]
    assert credited(rows_of_page(tmp_path, *layers), "l-s-1") == ["l-s-2"]


def test_one_credit_line_over_two_pictures_belongs_to_both(tmp_path):
    rows = rows_of_page(
        tmp_path,
        photo(1, 0, 4),
        photo(2, 4, 4),
        credit(3, 0.15, 7.5, "One archive, both pictures"),
    )

    assert credited(rows, "l-s-1") == ["l-s-3"]
    assert credited(rows, "l-s-2") == ["l-s-3"]


def test_a_picture_with_two_credit_lines_and_no_graphic_keeps_both(tmp_path):
    rows = rows_of_page(
        tmp_path,
        photo(1, 0, 6),
        credit(2, 0.15, 5, "Photo: A"),
        credit(3, 0.4, 5, "Colourised: B"),
    )

    assert credited(rows, "l-s-1") == ["l-s-2", "l-s-3"]


def test_a_credit_line_that_follows_no_layer_belongs_to_none(tmp_path):
    rows = rows_of_page(tmp_path, photo(1, 5, 4), credit(2, 0, 3, "Over nothing"))

    assert rows["l-s-1"].credits == []
