"""Builders shared by the archival tests."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from hyperframes_mcp.archival import (
    ArchivalCandidate,
    ArchivalDownloadError,
    Dimensions,
    Provenance,
    RightsStatus,
)
from tests.image_helpers import image_bytes

PD = RightsStatus.PUBLIC_DOMAIN
CC0 = RightsStatus.CC0
BY = RightsStatus.ATTRIBUTION_REQUIRED
SA = RightsStatus.SHARE_ALIKE
UNKNOWN = RightsStatus.UNKNOWN
RESTRICTED = RightsStatus.RESTRICTED

JPEG_HEADERS = {"content-type": "image/jpeg"}


def mock_client(client_cls, payload, *, requests=None, status=200, content=None):
    """A provider client whose every request returns ``payload`` as JSON.

    ``content`` (raw bytes) replaces the JSON body; ``requests`` collects the
    requests the client made.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=payload)

    return client_cls(transport=httpx.MockTransport(handler))


def make_candidate(
    rights_status: RightsStatus = RightsStatus.PUBLIC_DOMAIN,
    *,
    needs_review: bool | None = None,
    size: tuple[int, int] | None = None,
    raw_rights_fields: dict[str, Any] | None = None,
    **overrides: Any,
) -> ArchivalCandidate:
    """A valid candidate; ``unknown`` defaults to needs_review, others do not."""
    if needs_review is None:
        needs_review = rights_status is RightsStatus.UNKNOWN
    fields: dict[str, Any] = {
        "provider": "wikimedia",
        "provider_id": "1",
        "source_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
        "direct_file_url": "https://upload.wikimedia.org/example.jpg",
        "title": "Example",
        "creator": "A. Creator",
        "date": "1900",
        "dimensions": Dimensions(width=size[0], height=size[1]) if size else None,
        "licence_id": "CC-BY-4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0",
        "rights_status": rights_status,
        "needs_review": needs_review,
        "provenance": Provenance(
            retrieved_at=datetime(2026, 9, 30, tzinfo=UTC),
            api_url="https://commons.wikimedia.org/w/api.php?q=example",
            raw_rights_fields=(raw_rights_fields if raw_rights_fields is not None else {"License": "example"}),
        ),
    }
    fields.update(overrides)
    return ArchivalCandidate(**fields)


class FakeProvider:
    def __init__(self, name: str, results=None, error: Exception | None = None):
        self.name = name
        self.results = results or []
        self.error = error
        self.queries: list[str] = []
        self.closed = False

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.results)

    async def close(self) -> None:
        self.closed = True


class FakeDownloader:
    """Writes a real tiny picture for a candidate; ``fail`` ids raise a download error."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.calls: list[str] = []

    async def __call__(self, candidate: ArchivalCandidate, dest_dir: Path) -> Path:
        self.calls.append(candidate.provider_id)
        if candidate.provider_id in self.fail:
            raise ArchivalDownloadError("boom")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{candidate.provider}_{candidate.provider_id}.png"
        path.write_bytes(image_bytes())
        return path


class QueryProvider:
    """A provider that answers each query with its own results; a query it does not know finds nothing."""

    def __init__(self, name: str, by_query: dict[str, list], *, ranks_by_content: bool = False):
        self.name = name
        self.by_query = by_query
        self.ranks_by_content = ranks_by_content
        self.queries: list[str] = []

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        self.queries.append(query)
        return list(self.by_query.get(query, []))

    async def close(self) -> None:
        pass
