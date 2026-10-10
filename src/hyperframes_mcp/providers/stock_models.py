"""Data models for stock media candidates and search results."""

from pydantic import BaseModel, ConfigDict


class StockCandidate(BaseModel):
    """A candidate stock media item from a provider search."""

    model_config = ConfigDict(frozen=True)

    provider: str
    provider_id: str
    media_type: str
    title: str
    thumbnail_url: str
    source_url: str
    width: int
    height: int
    photographer: str
    duration_seconds: float | None = None
    relevance_score: float = 0.0


class StockSearchResult(BaseModel):
    """Result set from a stock media provider search."""

    model_config = ConfigDict(frozen=True)

    query: str
    provider: str
    candidates: list[StockCandidate]
    total_results: int
