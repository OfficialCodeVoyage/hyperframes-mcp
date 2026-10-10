"""The narration's word timings: loading, cue resolution (repeats, offsets, punctuation) and the local aligner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperframes_mcp.motion.timing import (
    AlignmentError,
    CueError,
    Word,
    load_alignment,
    resolve_cue,
)

SAY = "On April 12, 1945, Eisenhower rode the single-cable elevator. The gold was in the mine. The gold was gone."


def words_of(text: str, step: float = 0.5) -> list[Word]:
    """An alignment of ``text`` with each word ``step`` seconds after the one before it."""
    return [Word(w, round(i * step, 3), round(i * step + step * 0.8, 3)) for i, w in enumerate(text.split())]


def write(path: Path, data) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_alignment_reads_a_list_of_timed_words(tmp_path):
    path = write(
        tmp_path / "w.json",
        [
            {"word": "On", "start": 0.0, "end": 0.2},
            {"word": "April", "start": 0.3, "end": 0.7},
        ],
    )

    assert load_alignment(path) == [Word("On", 0.0, 0.2), Word("April", 0.3, 0.7)]


@pytest.mark.parametrize(
    "data, message",
    [
        ({"word": "x"}, "JSON list"),
        ([["x", 0, 1]], "object with word, start and end"),
        ([{"word": "x", "start": 0, "end": 1, "extra": 1}], "object with word"),
        ([{"word": 3, "start": 0, "end": 1}], "text word"),
        ([{"word": "x", "start": -1, "end": 1}], "zero or more"),
        ([{"word": "x", "start": True, "end": 1}], "text word"),
        ([{"word": "x", "start": 2, "end": 1}], "ends before it starts"),
        (
            [
                {"word": "a", "start": 1, "end": 2},
                {"word": "b", "start": 0.5, "end": 2},
            ],
            "starts before the word before",
        ),
    ],
)
def test_load_alignment_refuses_a_bad_entry_and_names_it(tmp_path, data, message):
    with pytest.raises(AlignmentError, match=message):
        load_alignment(write(tmp_path / "w.json", data))


def test_load_alignment_refuses_a_missing_or_broken_file(tmp_path):
    with pytest.raises(AlignmentError, match="cannot read"):
        load_alignment(tmp_path / "nope.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(AlignmentError, match="cannot read"):
        load_alignment(broken)


def test_a_cue_resolves_to_the_start_of_its_first_word():
    alignment = words_of(SAY)

    assert resolve_cue(alignment, "Eisenhower") == 2.0
    assert resolve_cue(alignment, "on april 12") == 0.0


def test_a_cue_ignores_case_punctuation_and_how_words_are_split():
    alignment = words_of(SAY)

    # a comma after the date, an upper case start, a hyphen
    assert resolve_cue(alignment, "APRIL 12 1945") == 0.5
    assert resolve_cue(alignment, "the single cable elevator") == 3.0
    split = [Word("single", 1.0, 1.2), Word("-cable", 1.3, 1.5), Word("lift", 1.6, 1.9)]
    assert resolve_cue(split, "single-cable lift") == 1.0
    number = [Word("8", 0.0, 0.2), Word("198", 0.3, 0.6), Word("bars", 0.7, 0.9)]
    assert resolve_cue(number, "8,198 bars") == 0.0


def test_a_cue_matches_whole_words_only():
    alignment = [Word("gold", 0.0, 0.3), Word("goldfish", 1.0, 1.3)]

    assert resolve_cue(alignment, "gold") == 0.0
    with pytest.raises(CueError, match="not in the narration"):
        resolve_cue(alignment, "fish")


def test_a_repeated_phrase_is_picked_by_its_occurrence():
    alignment = words_of(SAY)

    assert resolve_cue(alignment, "the gold was", occurrence=1) == 4.5
    assert resolve_cue(alignment, "the gold was", occurrence=2) == 7.5


def test_an_occurrence_that_does_not_exist_says_how_often_the_phrase_is_said():
    with pytest.raises(CueError, match=r"said 2 time\(s\), so occurrence 3"):
        resolve_cue(words_of(SAY), "the gold was", occurrence=3)


def test_an_offset_moves_the_cue_and_a_negative_result_is_refused():
    alignment = words_of(SAY)

    assert resolve_cue(alignment, "Eisenhower", offset=-0.4) == pytest.approx(1.6)
    assert resolve_cue(alignment, "Eisenhower", offset=0.25) == pytest.approx(2.25)
    with pytest.raises(CueError, match="before the start of the voice"):
        resolve_cue(alignment, "on april", offset=-0.1)


def test_a_phrase_that_is_not_said_is_a_clear_error():
    with pytest.raises(CueError, match="'Reichsmarks'.*not in the narration"):
        resolve_cue(words_of(SAY), "Reichsmarks")
    with pytest.raises(CueError, match="at least one letter"):
        resolve_cue(words_of(SAY), " ,. ")
    with pytest.raises(CueError, match="occurrence must be 1 or more"):
        resolve_cue(words_of(SAY), "gold", occurrence=0)


def test_a_phrase_does_not_match_across_a_gap_in_the_words():
    alignment = [Word("the", 0, 0.2), Word("old", 0.3, 0.5), Word("gold", 0.6, 0.9)]

    with pytest.raises(CueError):
        resolve_cue(alignment, "the gold")


# -- the aligner ------------------------------------------------------------------------------------------

WHISPER_JSON = {
    "transcription": [
        {
            "text": " On",
            "offsets": {"from": 100, "to": 400},
            "tokens": [{"text": " On", "t_dtw": 12}],
        },
        {
            "text": " April",
            "offsets": {"from": 400, "to": 900},
            "tokens": [
                {"text": " April", "t_dtw": 45},
                {"text": "[_TT_9]", "t_dtw": 5},
            ],
        },
        {"text": "[BLANK_AUDIO]", "offsets": {"from": 900, "to": 1000}, "tokens": []},
        {"text": " twelve", "offsets": {"from": 1200, "to": 1700}, "tokens": []},
    ]
}


# -- the ElevenLabs aligner (a fake client: no key, no network) -------------------------------------------


class FakeForcedAlignment:
    """An SDK client whose forced-alignment endpoint answers with the given ``(text, start, end)`` items."""

    def __init__(self, *items: tuple[str, float, float], error: Exception | None = None):
        self.forced_alignment = self
        self.items, self.error, self.calls = items, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(words=[SimpleNamespace(text=t, start=s, end=e) for t, s, e in self.items])
