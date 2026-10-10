"""The narration word by word: the words inside a stretch, and where a phrase is said."""

from __future__ import annotations

import pytest

from hyperframes_mcp.motion.narration import find_phrase, words_between
from hyperframes_mcp.motion.timing import Word

WORDS = [
    Word("At", 19.2, 19.3),
    Word("twenty", 19.3, 19.7),
    Word("seconds", 19.7, 20.1),
    Word("dynamite", 20.1, 20.9),
    Word("inside,", 21.8, 22.2),
    Word("eight", 24.1, 24.4),
    Word("thousand", 24.4, 24.9),
    Word("one", 24.9, 25.0),
    Word("hundred", 25.0, 25.3),
    Word("ninety-eight", 25.3, 26.1),
    Word("gold", 26.1, 26.4),
    Word("bars.", 26.4, 26.9),
]


def test_a_stretch_keeps_the_words_that_are_said_in_it_at_least_in_part():
    inside = words_between(WORDS, 20.5, 22.0)

    assert [w.word for w in inside] == [
        "dynamite",
        "inside,",
    ]  # "dynamite" ends at 20.9
    assert words_between(WORDS, None, None) == WORDS
    assert [w.word for w in words_between(WORDS, 26.0, None)] == [
        "ninety-eight",
        "gold",
        "bars.",
    ]
    assert words_between(WORDS, 40, 50) == []


def test_a_phrase_is_found_with_the_time_it_starts_and_ends_whatever_the_punctuation():
    (found,) = find_phrase(WORDS, "Eight thousand, one hundred NINETY-EIGHT")

    assert found.text == "eight thousand one hundred ninety-eight"
    assert (found.start, found.end) == (24.1, 26.1)
    assert found.words[0].word == "eight"


def test_a_part_of_a_hyphenated_word_is_found_and_the_whole_word_is_the_match():
    (found,) = find_phrase(WORDS, "hundred ninety")

    assert found.text == "hundred ninety-eight"  # the words as said, whole
    assert (found.start, found.end) == (25.0, 26.1)


def test_a_phrase_that_is_not_said_gives_nothing_and_a_blank_one_is_refused():
    assert find_phrase(WORDS, "dynamite gold") == []
    assert find_phrase(WORDS, "brick") == []
    with pytest.raises(ValueError, match="phrase"):
        find_phrase(WORDS, "  ,. ")


def test_a_phrase_said_twice_is_found_both_times_in_order():
    twice = [Word("gold", 1.0, 1.4), Word("and", 1.4, 1.5), Word("gold.", 2.0, 2.4)]

    found = find_phrase(twice, "gold")

    assert [(f.start, f.end) for f in found] == [(1.0, 1.4), (2.0, 2.4)]


def test_a_curly_or_straight_apostrophe_and_quoted_words_match_each_other():
    said = [
        Word("Germany\u2019s", 1.0, 1.4),
        Word("\u2018gold\u2019", 1.4, 1.8),
        Word("left.", 1.8, 2.1),
    ]

    (found,) = find_phrase(said, "Germany's gold")
    (quoted,) = find_phrase(said, "gold left")

    assert (found.start, found.end) == (1.0, 1.8)
    assert (quoted.start, quoted.end) == (1.4, 2.1)
