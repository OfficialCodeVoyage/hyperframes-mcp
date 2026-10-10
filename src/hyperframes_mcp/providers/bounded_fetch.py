"""Bounded in-memory download of a third-party URL.

For the image clients that decode a download straight from memory (map search results, the fal.ai
image result). The bounds are the stock downloader's: https only (the first URL and every
redirect target, checked before the hop is made), a byte cap taken from
``visuals.stock_download.max_photo_mb`` and a deadline for the whole download including
redirects (``timeout_seconds``). No credentials are sent.
"""

from __future__ import annotations

import asyncio
import io
from functools import cache
from pathlib import Path

import httpx
from PIL import Image

from hyperframes_mcp.providers.downloader import (
    MAX_REDIRECTS,
    DownloadLimits,
    StockDownloadError,
    load_download_limits,
    require_https,
)


@cache
def _limits() -> DownloadLimits:
    """The stock download limits, read from settings.yaml once per process."""
    return load_download_limits()


async def get_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    total_timeout: float | None = None,
) -> bytes:
    """GET ``url`` with ``client`` and return the body, within the limits.

    Args:
        client: The caller's client. Redirects are followed here, one checked hop at a time,
            whatever the client's own setting.
        url: Must be https, as must every redirect target.
        total_timeout: Deadline in seconds for the whole download; default from settings.

    Raises:
        StockDownloadError: a non-https URL or redirect, too many redirects, an HTTP error,
            a body over the cap (declared or streamed) or the deadline passed.
    """
    limits = _limits()
    cap = limits.max_photo_bytes
    deadline = limits.total_timeout if total_timeout is None else total_timeout
    require_https(url)
    try:
        async with asyncio.timeout(deadline):
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        require_https(url)
                        continue
                    response.raise_for_status()
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > cap:
                        raise StockDownloadError(f"download declares {declared} bytes, over the {cap}-byte limit")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > cap:
                            raise StockDownloadError(f"download is larger than the {cap}-byte limit")
                    return bytes(body)
            raise StockDownloadError(f"more than {MAX_REDIRECTS} redirects")
    except TimeoutError as exc:
        raise StockDownloadError(f"download timed out after {deadline:g}s") from exc
    except httpx.HTTPError as exc:
        raise StockDownloadError(f"download failed: {type(exc).__name__}") from exc


def save_shrunk_jpeg(content: bytes, output_path: str, max_dimension: int) -> None:
    """Decode ``content``, shrink it to fit ``max_dimension`` and save it as a JPEG.

    Blocking Pillow work: an async caller runs it with ``asyncio.to_thread`` so the decode
    does not stall the event loop.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    img = Image.open(io.BytesIO(content))
    if img.width > max_dimension or img.height > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.save(output_path, "JPEG", quality=90)
