"""Library of Congress (loc.gov JSON API) archival client.

Searches ``https://www.loc.gov/photos/?fo=json``. The search response already
carries each item's rights statements (``item.rights_advisory`` and
``item.rights_information``) and sized image URLs, so no per-item request is
needed.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from hyperframes_mcp.archival.rights import assess_loc
from hyperframes_mcp.archival.textutil import (
    clean_date,
    join_text,
    text_list,
)

logger = logging.getLogger(__name__)

_ITEM_URL = re.compile(r"^https://www\.loc\.gov/(?:item|resource)/(?P<id>[^/?#]+)")
_RASTER_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp")
_RIGHTS_ITEM_FIELDS = ("rights_advisory", "rights_information", "rights")


def _best_image(value: Any) -> tuple[str, Dimensions | None] | None:
    """The largest raster image URL in ``image_url``, with its size if stated.

    LoC lists derivatives of one image and states each size in the URL
    fragment (``#h=425&w=640``). Placeholder icons under ``/static/images/``
    (used for groups of images) are not images of the item and are skipped.
    """
    options = []
    for index, raw in enumerate(text_list(value)):
        base, _, fragment = raw.partition("#")
        parsed = urlparse(base)
        path = parsed.path.lower()
        if parsed.scheme != "https" or "/static/images/" in path or not path.endswith(_RASTER_SUFFIXES):
            continue
        size = parse_qs(fragment)
        dims = Dimensions.parse(size.get("w", [None])[0], size.get("h", [None])[0])
        options.append(((dims.pixels if dims else -1, index), base, dims))
    if not options:
        return None
    _, url, dims = max(options, key=lambda option: option[0])
    return url, dims


class LibraryOfCongressClient:
    """Async client for loc.gov image search with rights assessment."""

    name = "loc"
    SEARCH_URL = "https://www.loc.gov/photos/"

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client = build_http_client(user_agent=user_agent, timeout=timeout, transport=transport)

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        """Search loc.gov for digitised images matching ``query``.

        Results without a usable image file (groups of images, books without
        a page image, non-item pages) are skipped.

        Raises:
            ArchivalProviderError: the request failed or the response shape is
                not the loc.gov search shape.
        """
        data, api_url = await fetch_json(
            self._client,
            self._semaphore,
            self.SEARCH_URL,
            {"q": query, "fo": "json", "c": limit, "fa": "online-format:image"},
        )
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise ArchivalProviderError("loc.gov response has no results list")
        retrieved_at = utc_now()
        candidates = []
        for result in results[:limit]:
            if isinstance(result, dict) and (candidate := self._to_candidate(result, api_url, retrieved_at)):
                candidates.append(candidate)
        logger.info("loc.gov search %r returned %d candidates", query, len(candidates))
        return candidates

    @staticmethod
    def _to_candidate(result: dict[str, Any], api_url: str, retrieved_at: datetime) -> ArchivalCandidate | None:
        source_url = result.get("url")
        match = _ITEM_URL.match(source_url) if isinstance(source_url, str) else None
        image = _best_image(result.get("image_url"))
        if match is None or image is None:
            return None
        direct_url, dimensions = image

        item = result.get("item") if isinstance(result.get("item"), dict) else {}
        title = join_text(result.get("title")) or join_text(item.get("title")) or "Untitled"
        # LoC lists the main creator first, then added entries such as former
        # owners and collections; credit only the first.
        names = (
            [c["title"] for c in item.get("creators") or [] if isinstance(c, dict) and isinstance(c.get("title"), str)]
            or text_list(item.get("contributors"))
            or text_list(result.get("contributor"))
        )
        creator = join_text(names[:1])
        date = clean_date(result.get("date")) or clean_date(item.get("date"))

        rights_texts = [t for f in _RIGHTS_ITEM_FIELDS for t in text_list(item.get(f))]
        access_restricted = result.get("access_restricted") is True
        raw_rights = {f: item[f] for f in _RIGHTS_ITEM_FIELDS if f in item}
        if "access_restricted" in result:
            raw_rights["access_restricted"] = result["access_restricted"]
        assessment = assess_loc(rights_texts, access_restricted=access_restricted)

        candidate = ArchivalCandidate(
            provider="loc",
            provider_id=match["id"],
            source_url=source_url,
            direct_file_url=direct_url,
            title=title,
            creator=creator,
            date=date,
            description=join_text(result.get("description"), " "),
            dimensions=dimensions,
            licence_id=assessment.licence_id,
            licence_url=assessment.licence_url,
            rights_statement=" ".join(dict.fromkeys(t.strip() for t in rights_texts)) or None,
            rights_status=assessment.status,
            needs_review=assessment.needs_review,
            review_reasons=assessment.reasons,
            provenance=Provenance(
                retrieved_at=retrieved_at,
                api_url=api_url,
                raw_rights_fields=raw_rights,
            ),
        )
        return with_attribution(candidate)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
