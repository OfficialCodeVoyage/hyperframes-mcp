"""The narration word by word: the words said inside a stretch of the video, and when a phrase is said.

``layer_table`` gives the sentence spoken over a layer; placing a counter or a title on a word needs the word's own time,
which is here. The times are the alignment's (``timing.load_alignment``): seconds into the voice, which starts with the
video.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from hyperframes_mcp.motion.timing import Word

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_CURLY = str.maketrans({"\u2019": "'", "\u2018": "'"})


@dataclass(frozen=True)
class Match:
    """One place a phrase is said: the whole words it covers, as said, and when the first starts and the last ends."""

    text: str
    start: float
    end: float
    words: list[Word]


def words_between(words: Sequence[Word], from_s: float | None, to_s: float | None) -> list[Word]:
    """The words said, at least in part, between ``from_s`` and ``to_s`` (None: from the start, to the end)."""
    return [word for word in words if (from_s is None or word.end > from_s) and (to_s is None or word.start < to_s)]


def _tokens(text: str) -> list[str]:
    """The lower-case words of ``text``: letters and digits, an apostrophe inside a word kept (curly or straight)."""
    return _TOKEN.findall(text.lower().translate(_CURLY))


def find_phrase(words: Sequence[Word], phrase: str) -> list[Match]:
    """Every place ``phrase`` is said, in order. Case and punctuation do not matter, and a hyphenated word counts as its
    parts (``ninety`` finds ``ninety-eight``); the match is the whole words. Numbers are as the script wrote them
    (spelled out for the voice, or digits): when one finds nothing, try the other form.

    Raises:
        ValueError: ``phrase`` holds no word to look for.
    """
    wanted = _tokens(phrase)
    if not wanted:
        raise ValueError("the phrase has no words to look for")
    spoken = [(token, index) for index, word in enumerate(words) for token in _tokens(word.word)]
    found: list[Match] = []
    seen: set[tuple[int, int]] = set()
    for at in range(len(spoken) - len(wanted) + 1):
        if [token for token, _ in spoken[at : at + len(wanted)]] != wanted:
            continue
        first, last = spoken[at][1], spoken[at + len(wanted) - 1][1]
        if (first, last) in seen:
            continue
        seen.add((first, last))
        covered = list(words[first : last + 1])
        found.append(
            Match(
                " ".join(word.word for word in covered),
                covered[0].start,
                covered[-1].end,
                covered,
            )
        )
    return found
