"""Which archival pictures may go into a video, and how well they match a request.

Rules, in the order they apply to each candidate:

1. Only items that may go into a video without a human decision are considered: ``ArchivalCandidate.usable_by_default``
   (public domain, CC0, attribution-required, nothing flagged for review). Unknown, restricted (non-commercial,
   no-derivatives), share-alike and flagged items are dropped before ranking, so a licence nobody could establish
   never reaches a download. An item with no direct file URL, or too small for a 1080p frame, is dropped too
   (``unusable_reason``).
2. Relevance: the share of the query's words that appear in the candidate's title, description, creator and date
   (``relevance``). Below ``MIN_RELEVANCE`` the candidate is dropped: a wrong picture of the right century is worse
   than no picture.
"""

from __future__ import annotations

import re

from hyperframes_mcp.archival.models import ArchivalCandidate

# Share of the query's words a candidate's text must contain. A judgment, not a measured value:
# raise it if wrong pictures get through, lower it if too many cues fall back to stock.
MIN_RELEVANCE = 0.5
# Pictures narrower than this are dropped: a 1920-wide frame would show them soft.
MIN_WIDTH = 1000
# Results asked of each provider per cue.
RESULTS_PER_PROVIDER = 10

STOP_WORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "this",
        "that",
        "these",
        "those",
        "his",
        "her",
        "its",
        "their",
        "was",
        "were",
        "are",
        "has",
        "had",
        "have",
        "not",
        "but",
    ]
)


def _words(text: str) -> set[str]:
    """Lower-case words of ``text`` without stop words, with a trailing plural 's' removed."""
    found = set()
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in STOP_WORDS or (len(word) < 3 and not word.isdigit()):
            continue
        found.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return found


def relevance(query: str, candidate: ArchivalCandidate) -> float:
    """The share of ``query``'s words found in the candidate's descriptive text (0 to 1)."""
    wanted = _words(query)
    if not wanted:
        return 0.0
    text = " ".join(
        part
        for part in (
            candidate.title,
            candidate.description,
            candidate.creator,
            candidate.date,
        )
        if part
    )
    return len(wanted & _words(text)) / len(wanted)


def unusable_reason(candidate: ArchivalCandidate) -> str | None:
    """Why a candidate may not go into a video, or None when it may."""
    if not candidate.direct_file_url:
        return "no direct file URL"
    if not candidate.usable_by_default:
        reasons = "; ".join(candidate.review_reasons)
        return f"rights {candidate.rights_status.value}" + (f" ({reasons})" if reasons else "")
    if candidate.dimensions and candidate.dimensions.width < MIN_WIDTH:
        return f"only {candidate.dimensions.width} px wide"
    return None
