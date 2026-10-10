"""Internet Archive archival client (advancedsearch plus the metadata API).

``advancedsearch.php`` lists matching image items; each hit is then looked up
with ``/metadata/{identifier}``, which carries the rights fields
(``licenseurl``, ``rights``, ``possible-copyright-status``, ...) and the file
list. Rights are uploader-supplied on the Archive, so only a recognised
``licenseurl`` can lift an item above ``unknown`` (see ``rights.py``).
"""

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote

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
from hyperframes_mcp.archival.rights import assess_internet_archive
from hyperframes_mcp.archival.textutil import clean_date, join_text

logger = logging.getLogger(__name__)

_RIGHTS_FIELDS = (
    "licenseurl",
    "rights",
    "possible-copyright-status",
    "copyright-region",
    "access-restricted-item",
)
# Preferred file formats, best first. Web-friendly formats come before TIFF.
_FORMAT_TIERS = (("JPEG", "PNG", "GIF"), ("TIFF",))
_UNKNOWN_CREATORS = frozenset({"unknown", "anonymous", "n/a"})


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_image_file(files: Any) -> dict[str, Any] | None:
    """The largest original image file, preferring web formats over TIFF."""
    if not isinstance(files, list):
        return None
    usable = [f for f in files if isinstance(f, dict) and isinstance(f.get("name"), str)]
    for source in ("original", "derivative"):
        for formats in _FORMAT_TIERS:
            matches = [f for f in usable if f.get("source") == source and f.get("format") in formats]
            if matches:
                return max(matches, key=lambda f: _int(f.get("size")) or 0)
    return None


class InternetArchiveClient:
    """Async client for Internet Archive image search with rights assessment."""

    name = "internet_archive"
    SEARCH_URL = "https://archive.org/advancedsearch.php"
    METADATA_URL = "https://archive.org/metadata/"
    DOWNLOAD_URL = "https://archive.org/download/"
    DETAILS_URL = "https://archive.org/details/"

    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client = build_http_client(user_agent=user_agent, timeout=timeout, transport=transport)

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        """Search the Archive for image items matching ``query``.

        Items whose metadata request fails, or that have no downloadable image
        file, are skipped.

        Raises:
            ArchivalProviderError: the search request failed or the response
                shape is not the advancedsearch shape.
        """
        data, _ = await fetch_json(
            self._client,
            self._semaphore,
            self.SEARCH_URL,
            [
                ("q", f"({query}) AND mediatype:image"),
                ("fl[]", "identifier"),
                ("rows", limit),
                ("page", 1),
                ("output", "json"),
            ],
        )
        response = data.get("response") if isinstance(data, dict) else None
        docs = response.get("docs") if isinstance(response, dict) else None
        if not isinstance(docs, list):
            raise ArchivalProviderError("advancedsearch response has no docs list")
        identifiers = [
            d["identifier"] for d in docs[:limit] if isinstance(d, dict) and isinstance(d.get("identifier"), str)
        ]

        fetched = await asyncio.gather(*(self._fetch_metadata(i) for i in identifiers))
        retrieved_at = utc_now()
        candidates = []
        for identifier, outcome in zip(identifiers, fetched, strict=True):
            if outcome is None:
                continue
            metadata, api_url = outcome
            if candidate := self._to_candidate(identifier, metadata, api_url, retrieved_at):
                candidates.append(candidate)
        logger.info("Archive search %r returned %d candidates", query, len(candidates))
        return candidates

    async def _fetch_metadata(self, identifier: str) -> tuple[dict[str, Any], str] | None:
        """The item's metadata response and URL, or ``None`` (logged) to skip it."""
        try:
            data, api_url = await fetch_json(
                self._client,
                self._semaphore,
                self.METADATA_URL + quote(identifier, safe=""),
            )
        except ArchivalProviderError as exc:
            logger.warning("Skipping %s: %s", identifier, exc)
            return None
        if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
            logger.warning("Skipping %s: no metadata object", identifier)
            return None
        return data, api_url

    def _to_candidate(
        self,
        identifier: str,
        response: dict[str, Any],
        api_url: str,
        retrieved_at: datetime,
    ) -> ArchivalCandidate | None:
        metadata = response["metadata"]
        image = _pick_image_file(response.get("files"))
        if image is None:
            return None
        access_restricted = str(metadata.get("access-restricted-item", "")).lower() == "true"
        direct_url = (
            None if access_restricted else f"{self.DOWNLOAD_URL}{quote(identifier, safe='')}/{quote(image['name'])}"
        )
        dimensions = Dimensions.parse(image.get("width"), image.get("height"))

        title = join_text(metadata.get("title")) or identifier
        creator = join_text(metadata.get("creator"))
        if creator and creator.lower() in _UNKNOWN_CREATORS:
            creator = None
        date = clean_date(metadata.get("date")) or clean_date(metadata.get("year"))
        source_url = self.DETAILS_URL + quote(identifier, safe="")
        rights_text = join_text(metadata.get("rights"), " ")

        assessment = assess_internet_archive(
            licenseurl=join_text(metadata.get("licenseurl")),
            rights=rights_text,
            access_restricted=access_restricted,
        )
        candidate = ArchivalCandidate(
            provider="internet_archive",
            provider_id=identifier,
            source_url=source_url,
            direct_file_url=direct_url,
            title=title,
            creator=creator,
            date=date,
            description=join_text(metadata.get("description"), " "),
            dimensions=dimensions,
            licence_id=assessment.licence_id,
            licence_url=assessment.licence_url,
            rights_statement=rights_text,
            rights_status=assessment.status,
            needs_review=assessment.needs_review,
            review_reasons=assessment.reasons,
            provenance=Provenance(
                retrieved_at=retrieved_at,
                api_url=api_url,
                raw_rights_fields={f: metadata[f] for f in _RIGHTS_FIELDS if f in metadata},
            ),
        )
        return with_attribution(candidate)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
