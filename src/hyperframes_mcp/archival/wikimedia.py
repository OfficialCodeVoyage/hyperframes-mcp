"""Wikimedia Commons (MediaWiki API) archival client.

Uses ``generator=search`` over the File namespace with
``prop=imageinfo&iiprop=url|size|mime|extmetadata``. The licence fields come
from ``extmetadata`` (``LicenseShortName``, ``LicenseUrl``, ``License``,
``Copyrighted``, ``Restrictions``, ...), which Commons derives from the
templates on each file's description page.
"""

import asyncio
import logging
import re
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
from hyperframes_mcp.archival.rights import assess_wikimedia
from hyperframes_mcp.archival.textutil import clean_date, strip_html

logger = logging.getLogger(__name__)

# Fields asked of extmetadata. The rights ones are also stored verbatim in
# provenance.raw_rights_fields.
_RIGHTS_FIELDS = (
    "LicenseShortName",
    "LicenseUrl",
    "License",
    "UsageTerms",
    "AttributionRequired",
    "Attribution",
    "Copyrighted",
    "Restrictions",
    "Credit",
    "Artist",
)
_DESCRIPTIVE_FIELDS = ("ImageDescription", "ObjectName", "DateTimeOriginal")
_TALK_SUFFIX = re.compile(r"\s*\((?:talk|contribs?)\)", re.IGNORECASE)


def _flatten_extmetadata(extmetadata: Any) -> dict[str, str]:
    """``{field: {"value": ...}}`` to ``{field: str}``, dropping empty values."""
    if not isinstance(extmetadata, dict):
        return {}
    flat = {}
    for field, entry in extmetadata.items():
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(value, (str, int, float, bool)) and str(value) != "":
            flat[field] = str(value)
    return flat


class WikimediaCommonsClient:
    """Async client for Wikimedia Commons image search with rights assessment."""

    name = "wikimedia"
    API_URL = "https://commons.wikimedia.org/w/api.php"

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
        """Search Commons for bitmap images matching ``query``.

        Commons search requires every word. When a query of two or more words finds nothing it is
        searched once more with its first half (rounded up): ``gold rush miners california 1849``
        retries as ``gold rush miners``. Cue queries run subject, place, year, so the tail goes
        first. At most one extra request; the service still checks relevance against the full query.

        Raises:
            ArchivalProviderError: a request failed or the response shape is
                not the MediaWiki query shape.
        """
        candidates = await self._search_once(query, limit)
        words = query.split()
        if not candidates and len(words) > 1:
            shorter = " ".join(words[: (len(words) + 1) // 2])
            logger.info("Commons search %r found nothing; retrying as %r", query, shorter)
            candidates = await self._search_once(shorter, limit)
        return candidates

    async def _search_once(self, query: str, limit: int) -> list[ArchivalCandidate]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": "|".join(_RIGHTS_FIELDS + _DESCRIPTIVE_FIELDS),
        }
        data, api_url = await fetch_json(self._client, self._semaphore, self.API_URL, params)
        if not isinstance(data, dict):
            raise ArchivalProviderError("Commons response is not a JSON object")
        if "query" not in data:  # MediaWiki omits "query" when nothing matched
            return []
        pages = data["query"].get("pages") if isinstance(data["query"], dict) else None
        if not isinstance(pages, list):
            raise ArchivalProviderError("Commons response has no pages list")

        retrieved_at = utc_now()
        ordered = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: p.get("index", 0))
        candidates = []
        for page in ordered[:limit]:
            if candidate := self._to_candidate(page, api_url, retrieved_at):
                candidates.append(candidate)
        logger.info("Commons search %r returned %d candidates", query, len(candidates))
        return candidates

    @staticmethod
    def _to_candidate(page: dict[str, Any], api_url: str, retrieved_at: datetime) -> ArchivalCandidate | None:
        infos = page.get("imageinfo")
        info = infos[0] if isinstance(infos, list) and infos else None
        if not isinstance(info, dict):
            return None
        file_url = info.get("url")
        mime = info.get("mime")
        if (
            not isinstance(file_url, str)
            or not file_url.startswith("https://")
            or not isinstance(mime, str)
            or not mime.startswith("image/")
            or mime == "image/svg+xml"
        ):
            return None

        meta = _flatten_extmetadata(info.get("extmetadata"))
        file_title = str(page.get("title", "")).removeprefix("File:")
        title = strip_html(meta.get("ObjectName")) or file_title or "Untitled"
        creator = strip_html(meta.get("Artist"))
        if creator:
            creator = _TALK_SUFFIX.sub("", creator).strip() or None
        date = clean_date(meta.get("DateTimeOriginal"))
        source_url = info.get("descriptionurl")
        if not isinstance(source_url, str):
            source_url = f"https://commons.wikimedia.org/wiki/File:{file_title}"
        required_text = strip_html(meta.get("Attribution"))

        assessment = assess_wikimedia(meta)
        dimensions = Dimensions.parse(info.get("width"), info.get("height"))

        candidate = ArchivalCandidate(
            provider="wikimedia",
            provider_id=str(page.get("pageid", file_title)),
            source_url=source_url,
            direct_file_url=file_url,
            title=title,
            creator=creator,
            date=date,
            description=strip_html(meta.get("ImageDescription")),
            dimensions=dimensions,
            licence_id=assessment.licence_id,
            licence_url=assessment.licence_url,
            rights_statement=meta.get("UsageTerms") or meta.get("LicenseShortName"),
            rights_status=assessment.status,
            needs_review=assessment.needs_review,
            review_reasons=assessment.reasons,
            provenance=Provenance(
                retrieved_at=retrieved_at,
                api_url=api_url,
                raw_rights_fields={f: meta[f] for f in _RIGHTS_FIELDS if f in meta},
            ),
        )
        return with_attribution(candidate, required_text=required_text)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
