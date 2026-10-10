"""Openverse image search (api.openverse.org/v1/images/) as an archival provider.

Openverse indexes openly licensed pictures from museums, libraries and Flickr Commons, among them collections the other
archival providers do not search (Smithsonian, the Met, Rijksmuseum, the New York Public Library). It is used anonymously:
no key, no account (20 requests a minute and 200 a day; one search is one request, and a 429 is waited out by
``RetryingClient``). It does not verify the licences it indexes, so ``usable_by_default`` is only as good as the uploader's
word: a person opens ``source_url`` and confirms before use (the video MCP server's ``fetch_media`` says so in the record).

The licence is read by the sound module's recogniser (``assess_openverse_licence``: the code, the version and the deed URL
must agree, else ``unknown``), the same one that checks Openverse audio. The search asks only for CC0, the public-domain
mark and CC BY; each item is assessed again here, and ``ArchivalCandidate.usable_by_default`` is what lets it be used.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from hyperframes_mcp.archival.attribution import with_attribution
from hyperframes_mcp.archival.base import (
    DEFAULT_USER_AGENT,
    ArchivalProviderError,
    build_http_client,
    fetch_json,
    utc_now,
)
from hyperframes_mcp.archival.models import (
    ArchivalCandidate,
    Dimensions,
    Provenance,
)
from hyperframes_mcp.archival.sound_licence import assess_openverse_licence

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 20  # what Openverse gives an anonymous caller
_RIGHTS_FIELDS = (
    "license",
    "license_version",
    "license_url",
    "attribution",
    "provider",
    "source",
)


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _https(value: Any) -> str | None:
    text = _text(value)
    return text if text and text.startswith("https://") else None


def _tags(value: Any) -> str | None:
    """The item's tag names as one line of words, for the relevance check (Openverse matches on tags too)."""
    names = [
        name
        for tag in (value if isinstance(value, list) else [])
        if isinstance(tag, dict) and (name := _text(tag.get("name")))
    ]
    return ", ".join(names[:10]) or None


class OpenverseImageClient:
    """Async client for Openverse image search with rights assessment."""

    name = "openverse"
    API_URL = "https://api.openverse.org/v1/images/"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(1)
        self._client = build_http_client(user_agent=user_agent, timeout=timeout, transport=transport)

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        """Search Openverse for CC0, public-domain-mark and CC BY pictures matching ``query``.

        One page of at most ``limit`` items (1 to 20). An item without an id is skipped.

        Raises:
            ArchivalProviderError: the request failed or the response is not a search page.
        """
        data, api_url = await fetch_json(
            self._client,
            self._semaphore,
            self.API_URL,
            {
                "q": query,
                "license": "cc0,pdm,by",
                "page_size": min(max(limit, 1), MAX_PAGE_SIZE),
            },
        )
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ArchivalProviderError("Openverse response has no results list")
        retrieved_at = utc_now()
        candidates = [
            candidate for raw in results[:limit] if (candidate := self._to_candidate(raw, api_url, retrieved_at))
        ]
        logger.info("Openverse image search %r returned %d candidates", query, len(candidates))
        return candidates

    @staticmethod
    def _to_candidate(raw: Any, api_url: str, retrieved_at: datetime) -> ArchivalCandidate | None:
        identifier = _text(raw.get("id")) if isinstance(raw, dict) else None
        source_url = _https(raw.get("foreign_landing_url")) if identifier else None
        if identifier is None or source_url is None:
            return None
        code, version = _text(raw.get("license")), _text(raw.get("license_version"))
        assessment = assess_openverse_licence(code, version, _text(raw.get("license_url")))
        candidate = ArchivalCandidate(
            provider="openverse",
            provider_id=identifier,
            source_url=source_url,
            direct_file_url=_https(raw.get("url")),
            title=_text(raw.get("title")) or "Untitled",
            creator=_text(raw.get("creator")),
            description=_tags(raw.get("tags")),
            dimensions=Dimensions.parse(raw.get("width"), raw.get("height")),
            licence_id=assessment.licence_id,
            licence_url=assessment.licence_url,
            rights_statement=" ".join(p for p in (code, version) if p) or None,
            rights_status=assessment.status,
            needs_review=assessment.needs_review,
            review_reasons=assessment.reasons,
            provenance=Provenance(
                retrieved_at=retrieved_at,
                api_url=api_url,
                raw_rights_fields={f: raw[f] for f in _RIGHTS_FIELDS if raw.get(f) is not None},
            ),
        )
        return with_attribution(candidate)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
