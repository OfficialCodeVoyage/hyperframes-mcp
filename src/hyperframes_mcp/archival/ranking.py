"""Ranking of archival candidates: rights first, then resolution."""

from collections.abc import Iterable

from hyperframes_mcp.archival.models import ArchivalCandidate
from hyperframes_mcp.archival.rights import RIGHTS_ORDER


def _sort_key(candidate: ArchivalCandidate) -> tuple[int, bool, int]:
    pixels = candidate.dimensions.pixels if candidate.dimensions else 0
    return (RIGHTS_ORDER[candidate.rights_status], candidate.needs_review, -pixels)


def rank_candidates(candidates: Iterable[ArchivalCandidate], max_count: int | None = None) -> list[ArchivalCandidate]:
    """Order candidates best first.

    Order of precedence: rights tier (public domain and CC0, then
    attribution-required, share-alike, unknown, restricted), then items
    without a review flag before flagged ones, then more pixels first. Items
    of unknown size come last within their group. Ties keep input order.

    Args:
        candidates: Candidates from any providers.
        max_count: Return at most this many, or all when ``None``.
    """
    ranked = sorted(candidates, key=_sort_key)
    return ranked if max_count is None else ranked[:max_count]
