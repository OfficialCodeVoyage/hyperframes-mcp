"""Bounded download: https only, byte cap, total deadline, https redirect targets."""

import asyncio

import httpx
import pytest

from hyperframes_mcp.archival import (
    ArchivalDownloadError,
    DownloadLimits,
    download_candidate,
    download_file,
    download_image,
)
from tests.archival.helpers import JPEG_HEADERS, make_candidate

URL = "https://archive.org/download/item/item.jpg"


class _Stream(httpx.AsyncByteStream):
    """A body that yields ``chunks`` with an optional pause before each one."""

    def __init__(self, chunks, delay=0.0):
        self._chunks = chunks
        self._delay = delay

    async def __aiter__(self):
        for chunk in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield chunk


async def _download(handler, tmp_path, *, limits=None, url=URL, stem="item"):
    kwargs = {"limits": limits} if limits else {}
    return await download_image(url, tmp_path, stem, transport=httpx.MockTransport(handler), **kwargs)


@pytest.mark.asyncio
async def test_downloads_to_a_named_file_with_extension_from_content_type(tmp_path):
    def handler(request):
        return httpx.Response(200, headers=JPEG_HEADERS, content=b"\xff\xd8\xff" + b"x" * 100)

    path = await _download(handler, tmp_path / "out", stem="internet_archive_item")

    assert path == tmp_path / "out" / "internet_archive_item.jpg"
    assert path.read_bytes().startswith(b"\xff\xd8\xff")
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["internet_archive_item.jpg"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "suffix"),
    [
        ("image/png", ".png"),
        ("image/jpeg; charset=binary", ".jpg"),
        ("image/tiff", ".tif"),
        ("IMAGE/WEBP", ".webp"),
    ],
)
async def test_extension_follows_content_type(tmp_path, content_type, suffix):
    handler = lambda r: httpx.Response(200, headers={"content-type": content_type}, content=b"data")

    assert (await _download(handler, tmp_path)).suffix == suffix


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml", "application/octet-stream", ""])
async def test_non_image_or_unsupported_content_type_is_rejected_and_leaves_nothing(tmp_path, content_type):
    headers = {"content-type": content_type} if content_type else {}
    handler = lambda r: httpx.Response(200, headers=headers, content=b"<html>")

    with pytest.raises(ArchivalDownloadError, match="content type"):
        await _download(handler, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_http_url_is_refused_before_any_request(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    with pytest.raises(ArchivalDownloadError, match="non-https"):
        await _download(handler, tmp_path, url="http://archive.org/download/item/item.jpg")

    assert seen == []


@pytest.mark.asyncio
async def test_redirect_to_http_is_refused_and_never_requested(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "archive.org":
            return httpx.Response(302, headers={"location": "http://cdn.example.org/item.jpg"})
        return httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    with pytest.raises(ArchivalDownloadError, match="non-https"):
        await _download(handler, tmp_path)

    assert seen == [URL]  # the insecure target was never contacted
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_https_redirects_including_relative_ones_are_followed(tmp_path):
    def handler(request):
        if request.url.host == "archive.org":
            return httpx.Response(302, headers={"location": "https://ia800000.us.archive.org/1/item.jpg"})
        if request.url.path == "/1/item.jpg":
            return httpx.Response(301, headers={"location": "/2/item.jpg"})
        return httpx.Response(200, headers=JPEG_HEADERS, content=b"final")

    path = await _download(handler, tmp_path)

    assert path.read_bytes() == b"final"


@pytest.mark.asyncio
async def test_redirect_loop_is_cut_off(tmp_path):
    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(302, headers={"location": URL})

    with pytest.raises(ArchivalDownloadError, match="redirects"):
        await _download(handler, tmp_path, limits=DownloadLimits(max_redirects=3))

    assert len(hits) == 4  # the first request plus three redirects


@pytest.mark.asyncio
async def test_declared_size_over_the_cap_is_rejected_without_reading_the_body(
    tmp_path,
):
    def handler(request):
        return httpx.Response(
            200,
            headers={**JPEG_HEADERS, "content-length": "5000"},
            stream=_Stream([b"x"] * 1),
        )

    with pytest.raises(ArchivalDownloadError, match="byte cap"):
        await _download(handler, tmp_path, limits=DownloadLimits(max_bytes=1000))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_body_over_the_cap_without_a_content_length_is_stopped_and_cleaned_up(
    tmp_path,
):
    def handler(request):
        return httpx.Response(200, headers=JPEG_HEADERS, stream=_Stream([b"x" * 400] * 10))

    with pytest.raises(ArchivalDownloadError, match="byte cap"):
        await _download(handler, tmp_path, limits=DownloadLimits(max_bytes=1000))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_a_body_exactly_at_the_cap_is_accepted(tmp_path):
    handler = lambda r: httpx.Response(200, headers=JPEG_HEADERS, content=b"x" * 1000)

    path = await _download(handler, tmp_path, limits=DownloadLimits(max_bytes=1000))

    assert path.stat().st_size == 1000


@pytest.mark.asyncio
async def test_total_deadline_covers_a_slow_body_and_cleans_up(tmp_path):
    def handler(request):
        return httpx.Response(200, headers=JPEG_HEADERS, stream=_Stream([b"x"] * 50, delay=0.02))

    with pytest.raises(ArchivalDownloadError, match="deadline"):
        await _download(handler, tmp_path, limits=DownloadLimits(total_timeout=0.1))

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_total_deadline_covers_redirect_hops_too(tmp_path):
    async def handler(request):
        await asyncio.sleep(0.04)
        return httpx.Response(302, headers={"location": URL})

    with pytest.raises(ArchivalDownloadError, match="deadline"):
        await _download(
            handler,
            tmp_path,
            limits=DownloadLimits(total_timeout=0.1, max_redirects=50),
        )


@pytest.mark.asyncio
async def test_http_error_status_raises_and_leaves_nothing(tmp_path):
    with pytest.raises(ArchivalDownloadError):
        await _download(lambda r: httpx.Response(404), tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_connection_error_mid_stream_leaves_no_partial_file(tmp_path):
    def handler(request):
        raise httpx.ReadError("connection reset", request=request)

    with pytest.raises(ArchivalDownloadError):
        await _download(handler, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_empty_body_is_rejected(tmp_path):
    handler = lambda r: httpx.Response(200, headers=JPEG_HEADERS, content=b"")

    with pytest.raises(ArchivalDownloadError, match="empty"):
        await _download(handler, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_sends_user_agent_and_no_credentials(tmp_path):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    await _download(handler, tmp_path)

    assert "hyperframes-mcp" in seen["user-agent"]
    assert "authorization" not in seen
    assert "cookie" not in seen


@pytest.mark.asyncio
async def test_file_name_cannot_escape_the_destination_directory(tmp_path):
    handler = lambda r: httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    path = await _download(handler, tmp_path / "out", stem="../../etc/passwd")

    assert path.parent == tmp_path / "out"
    assert not path.name.startswith(".")


@pytest.mark.asyncio
async def test_download_candidate_names_the_file_after_provider_and_id(tmp_path):
    candidate = make_candidate(provider="internet_archive", provider_id="cmlpl_000770", direct_file_url=URL)
    handler = lambda r: httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    path = await download_candidate(candidate, tmp_path, transport=httpx.MockTransport(handler))

    assert path.name == "internet_archive_cmlpl_000770.jpg"


@pytest.mark.asyncio
async def test_download_candidate_without_a_direct_url_raises_without_a_request(
    tmp_path,
):
    def handler(request):
        raise AssertionError("no request expected")

    candidate = make_candidate(direct_file_url=None)

    with pytest.raises(ArchivalDownloadError, match="no direct file URL"):
        await download_candidate(candidate, tmp_path, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_download_file_accepts_the_content_types_it_is_given(tmp_path):
    handler = lambda r: httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"ID3")

    path = await download_file(
        URL,
        tmp_path,
        "clip",
        content_types={"audio/mpeg": ".mp3"},
        transport=httpx.MockTransport(handler),
    )

    assert path == tmp_path / "clip.mp3"
    assert path.read_bytes() == b"ID3"


@pytest.mark.asyncio
async def test_download_file_refuses_a_type_outside_the_ones_it_is_given(tmp_path):
    handler = lambda r: httpx.Response(200, headers=JPEG_HEADERS, content=b"x")

    with pytest.raises(ArchivalDownloadError, match="content type"):
        await download_file(
            URL,
            tmp_path,
            "clip",
            content_types={"audio/mpeg": ".mp3"},
            transport=httpx.MockTransport(handler),
        )

    assert list(tmp_path.iterdir()) == []
