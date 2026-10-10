"""Shorter, still true forms of a credit line, for a picture that leaves its credit too little time to be read.

A credit reads ``Creator, Source. Licence.`` (a CC BY one ``"Title" by Creator, Source. Licence.``). The forms drop what
a viewer needs least, in order: the site the picture was fetched from, then an agency's long name, then the credit's
last part (the source, when no site was dropped). The creator stays whole, also one written ``Surname, Given``. The
licence is never dropped or changed, and a CC BY credit keeps its title and creator: only a source's host goes. The full
text stays in the file's record, which the publish package reads.
"""

from __future__ import annotations

# the places a picture is fetched from: not who made it, so the first thing a short credit can lose
_HOSTS = frozenset(
    {
        "wikimedia commons",
        "commons.wikimedia.org",
        "internet archive",
        "archive.org",
        "openverse",
        "flickr",
        "flickr.com",
    }
)
# an agency's long official name and the shorter one people use
_NAMES = ((" and Records Administration", ""),)


def shorter_credits(text: str) -> list[str]:
    """The shorter forms of ``text``, longest first; empty when there is nothing that can go."""
    body, dot, licence = text.rpartition(". ")
    if not dot:
        return []
    ending = f". {licence}"
    parts = body.split(", ")
    kept = list(parts)
    while len(kept) > 1 and kept[-1].lower() in _HOSTS:
        kept.pop()
    steps = [kept]
    if not body.startswith('"'):
        steps.append([_shorten(part) for part in kept])
        if len(kept) == len(parts) > 1:
            steps.append([_shorten(part) for part in parts[:-1]])
    forms: list[str] = []
    for step in steps:
        form = ", ".join(step) + ending
        if form != text and form not in forms:
            forms.append(form)
    return forms


def _shorten(part: str) -> str:
    for old, new in _NAMES:
        part = part.replace(old, new)
    return part
