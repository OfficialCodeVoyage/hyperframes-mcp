"""The narration's word timings: load them, find a phrase in them, write them.

A shot list cuts to the voice by naming a phrase ("cue") instead of a number of seconds, so a different take of the
same script moves every cut by itself. The alignment is a JSON list of ``{word, start, end}`` (seconds into the
voice). Making one (forced alignment of a voice take against its script) is not done here: bring the file, from
whatever aligner you use.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.util.atomic import atomic_write_bytes


class AlignmentError(ValueError):
    """The alignment file is not a list of timed words."""


class CueError(ValueError):
    """A cue phrase is not in the narration, or resolves to a time before the start."""


@dataclass(frozen=True)
class Word:
    """One spoken word and when it is said, in seconds into the voice."""

    word: str
    start: float
    end: float


def _is_time(value: object) -> bool:
    return tokens.is_number(value) and value >= 0


def load_alignment(path: str | Path) -> list[Word]:
    """Read an alignment file: a JSON list of ``{word, start, end}``, starts never going backwards.

    Raises:
        AlignmentError: the file cannot be read, is not JSON, or an entry is not a word with times.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlignmentError(f"{path}: cannot read the alignment: {error}") from error
    if not isinstance(raw, list):
        raise AlignmentError(f"{path}: the alignment must be a JSON list of words")
    words: list[Word] = []
    for index, entry in enumerate(raw):
        at = f"{path}: word {index + 1}"
        if not isinstance(entry, dict) or set(entry) != {"word", "start", "end"}:
            raise AlignmentError(f"{at} must be an object with word, start and end")
        word, start, end = entry["word"], entry["start"], entry["end"]
        if not isinstance(word, str) or not _is_time(start) or not _is_time(end):
            raise AlignmentError(f"{at} needs a text word and start and end in seconds (zero or more)")
        if end < start:
            raise AlignmentError(f"{at} ({word!r}) ends before it starts")
        if words and start < words[-1].start:
            raise AlignmentError(f"{at} ({word!r}) starts before the word before it")
        words.append(Word(word, float(start), float(end)))
    return words


def _squash(text: str) -> str:
    """Lower case letters and digits only: case, punctuation, hyphens and apostrophes do not matter."""
    folded = unicodedata.normalize("NFKC", text).lower()
    return "".join(char for char in folded if char.isalnum())


def resolve_cue(alignment: list[Word], phrase: str, occurrence: int = 1, offset: float = 0.0) -> float:
    """The time in seconds at which the ``occurrence``-th spoken ``phrase`` starts, plus ``offset``.

    The match is on consecutive words and ignores case and punctuation (and where a word is split, so ``8,198`` and
    ``single-cable`` match however the tool wrote them). It starts and ends on word boundaries.

    Raises:
        CueError: the phrase is empty or not said (that many times), or the result is before the start of the voice.
    """
    target = _squash(phrase)
    if not target:
        raise CueError("a cue needs a phrase with at least one letter or digit")
    if occurrence < 1:
        raise CueError(f"cue {phrase!r}: occurrence must be 1 or more")
    spoken = [(w, s) for w in alignment if (s := _squash(w.word))]
    found: list[float] = []
    for first in range(len(spoken)):
        joined = ""
        for _, squashed in spoken[first:]:
            joined += squashed
            if not target.startswith(joined):
                break
            if joined == target:
                found.append(spoken[first][0].start)
                break
    if occurrence > len(found):
        said = (
            f"it is said {len(found)} time(s), so occurrence {occurrence} does not exist"
            if found
            else "it is not in the narration"
        )
        raise CueError(f"cue {phrase!r}: {said}")
    time = found[occurrence - 1] + offset
    if time < 0:
        raise CueError(f"cue {phrase!r} with offset {offset:g} is at {time:.3f} s, before the start of the voice")
    return time


def write_alignment(words: list[Word], out_json: str | Path) -> None:
    """Write ``words`` as the JSON list of ``{word, start, end}`` that :func:`load_alignment` reads."""
    text = json.dumps([{"word": w.word, "start": w.start, "end": w.end} for w in words], indent=1)
    atomic_write_bytes(out_json, (text + "\n").encode("utf-8"))
