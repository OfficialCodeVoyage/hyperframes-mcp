"""429 and 503 handling and timeout messages in the archival HTTP plumbing (offline, MockTransport).

``tests/archival/conftest.py`` replaces the sleep: the ``sleeps`` fixture holds the waits asked for.
"""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from hyperframes_mcp.archival import (
    ArchivalDownloadError,
    ArchivalProviderError,
    InternetArchiveClient,
    LibraryOfCongressClient,
    WikimediaCommonsClient,
    base,
    download_image,
)
from tests.archival.helpers import JPEG_HEADERS
from tests.archival.oracle import load_fixture

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
IMAGE_URL = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg"
OK = (200, {})


def _handler(replies, requests, ok):
    """Answer request n with ``replies[n]`` (the last one repeats); a reply is ``(status, headers)``.

    A 200 reply is ``ok()``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        status, headers = replies[min(len(requests), len(replies) - 1)]
        requests.append(request)
        if status == 200:
            return ok()
        return httpx.Response(status, headers=headers)

    return handler


def _search_result() -> httpx.Response:
    return httpx.Response(200, json=load_fixture("wikimedia_search.json"))


def _jpeg() -> httpx.Response:
    return httpx.Response(200, headers=JPEG_HEADERS, content=b"\xff\xd8\xff" + b"x" * 50)


def _wikimedia(replies, requests=None):
    requests = [] if requests is None else requests
    return WikimediaCommonsClient(transport=httpx.MockTransport(_handler(replies, requests, _search_result)))


@pytest.fixture
def frozen_now(monkeypatch):
    """The clock ``parse_retry_after`` measures an HTTP date against."""
    monkeypatch.setattr(base, "utc_now", lambda: NOW)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("5", 5.0),
        (" 12 ", 12.0),
        ("0", 0.0),
        ("soon", None),
        ("1.5", None),
        ("-3", None),
        ("", None),
        (None, None),
    ],
)
def test_retry_after_seconds_and_junk(value, seconds):
    assert base.parse_retry_after(value) == seconds


def test_retry_after_http_date_is_the_distance_from_now(frozen_now):
    ahead = format_datetime(NOW + timedelta(seconds=7), usegmt=True)
    behind = format_datetime(NOW - timedelta(seconds=30), usegmt=True)

    assert base.parse_retry_after(ahead) == 7.0
    assert base.parse_retry_after(behind) == 0.0


@pytest.mark.asyncio
async def test_429_with_retry_after_in_seconds_waits_that_long_then_succeeds(sleeps):
    requests = []
    client = _wikimedia([(429, {"Retry-After": "3"}), OK], requests)

    candidates = await client.search("standard oil")

    assert len(candidates) == 6
    assert len(requests) == 2
    assert sleeps == [3.0]


@pytest.mark.asyncio
async def test_429_with_retry_after_as_an_http_date_waits_until_then(sleeps, frozen_now):
    date = format_datetime(NOW + timedelta(seconds=7), usegmt=True)
    client = _wikimedia([(429, {"Retry-After": date}), OK])

    assert len(await client.search("standard oil")) == 6
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_a_retry_after_date_in_the_past_retries_at_once(sleeps, frozen_now):
    date = format_datetime(NOW - timedelta(minutes=5), usegmt=True)
    client = _wikimedia([(429, {"Retry-After": date}), OK])

    assert len(await client.search("standard oil")) == 6
    assert sleeps == [0.0]


@pytest.mark.asyncio
async def test_503_is_retried_like_429(sleeps):
    client = _wikimedia([(503, {"Retry-After": "1"}), OK])

    assert len(await client.search("standard oil")) == 6
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_retry_after_over_the_cap_fails_at_once_naming_host_and_value(sleeps):
    requests = []
    client = _wikimedia([(429, {"Retry-After": "600"})], requests)

    with pytest.raises(ArchivalProviderError) as raised:
        await client.search("standard oil")

    message = str(raised.value)
    assert "commons.wikimedia.org" in message
    assert "Retry-After: 600" in message
    assert "429" in message
    assert len(requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_after_date_over_the_cap_names_the_date(sleeps, frozen_now):
    date = format_datetime(NOW + timedelta(seconds=600), usegmt=True)
    client = _wikimedia([(503, {"Retry-After": date})])

    with pytest.raises(ArchivalProviderError, match="commons.wikimedia.org") as raised:
        await client.search("standard oil")

    assert date in str(raised.value)
    assert sleeps == []


@pytest.mark.asyncio
async def test_the_cap_itself_is_waited_and_one_second_more_is_not(sleeps):
    cap = base.MAX_RETRY_WAIT
    at_cap = _wikimedia([(429, {"Retry-After": str(int(cap))}), OK])
    over_cap = _wikimedia([(429, {"Retry-After": str(int(cap) + 1)}), OK])

    assert len(await at_cap.search("standard oil")) == 6
    assert sleeps == [cap]
    with pytest.raises(ArchivalProviderError, match="Retry-After"):
        await over_cap.search("standard oil")
    assert sleeps == [cap]


@pytest.mark.asyncio
async def test_without_retry_after_it_backs_off_one_then_two_seconds_and_gives_up(
    sleeps,
):
    requests = []
    client = _wikimedia([(429, {})], requests)

    with pytest.raises(ArchivalProviderError, match="429"):
        await client.search("standard oil")

    assert len(requests) == base.MAX_ATTEMPTS == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_an_unreadable_retry_after_falls_back_to_the_backoff(sleeps):
    client = _wikimedia([(429, {"Retry-After": "soon"}), OK])

    assert len(await client.search("standard oil")) == 6
    assert sleeps == [1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404, 500, 502])
async def test_other_errors_are_not_retried(sleeps, status):
    requests = []
    client = _wikimedia([(status, {"Retry-After": "1"})], requests)

    with pytest.raises(ArchivalProviderError):
        await client.search("standard oil")

    assert len(requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_cls", "payload"),
    [
        (LibraryOfCongressClient, {"results": []}),
        (WikimediaCommonsClient, {"batchcomplete": True}),
        (InternetArchiveClient, {"response": {"docs": []}}),
    ],
)
async def test_every_client_waits_out_a_429(sleeps, client_cls, payload):
    requests = []
    handler = _handler(
        [(429, {"Retry-After": "2"}), OK],
        requests,
        lambda: httpx.Response(200, json=payload),
    )
    client = client_cls(transport=httpx.MockTransport(handler))

    assert await client.search("erie") == []
    assert len(requests) == 2
    assert sleeps == [2.0]


async def _download(replies, tmp_path, requests):
    return await download_image(
        IMAGE_URL,
        tmp_path,
        "item",
        transport=httpx.MockTransport(_handler(replies, requests, _jpeg)),
    )


@pytest.mark.asyncio
async def test_download_waits_out_a_429_and_saves_the_file(tmp_path, sleeps):
    requests = []

    path = await _download([(429, {"Retry-After": "2"}), OK], tmp_path, requests)

    assert path.read_bytes().startswith(b"\xff\xd8\xff")
    assert len(requests) == 2
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_download_with_retry_after_over_the_cap_fails_naming_host_and_value(tmp_path, sleeps):
    requests = []

    with pytest.raises(ArchivalDownloadError) as raised:
        await _download([(429, {"Retry-After": "600"})], tmp_path, requests)

    message = str(raised.value)
    assert "upload.wikimedia.org" in message
    assert "Retry-After: 600" in message
    assert len(requests) == 1
    assert sleeps == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_gives_up_after_the_attempts_and_leaves_nothing(tmp_path, sleeps):
    requests = []

    with pytest.raises(ArchivalDownloadError, match="503"):
        await _download([(503, {})], tmp_path, requests)

    assert len(requests) == base.MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0]
    assert list(tmp_path.iterdir()) == []


def _times_out(request):
    raise httpx.ReadTimeout("", request=request)  # httpx's own timeouts carry no text


@pytest.mark.asyncio
async def test_a_search_timeout_message_names_the_exception_type():
    client = WikimediaCommonsClient(transport=httpx.MockTransport(_times_out))

    with pytest.raises(ArchivalProviderError) as raised:
        await client.search("standard oil")

    assert str(raised.value).endswith("failed: ReadTimeout")
    assert "commons.wikimedia.org" in str(raised.value)


@pytest.mark.asyncio
async def test_an_error_with_text_keeps_the_text_after_the_type():
    def refuses(request):
        raise httpx.ConnectError("name not resolved", request=request)

    client = WikimediaCommonsClient(transport=httpx.MockTransport(refuses))

    with pytest.raises(ArchivalProviderError) as raised:
        await client.search("standard oil")

    assert str(raised.value).endswith("failed: ConnectError: name not resolved")
    assert raised.value.status is None


@pytest.mark.asyncio
async def test_a_provider_error_carries_the_http_status():
    client = WikimediaCommonsClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))

    with pytest.raises(ArchivalProviderError) as raised:
        await client.search("standard oil")

    assert raised.value.status == 404


@pytest.mark.asyncio
async def test_a_download_timeout_message_names_the_exception_type(tmp_path):
    with pytest.raises(ArchivalDownloadError) as raised:
        await download_image(IMAGE_URL, tmp_path, "item", transport=httpx.MockTransport(_times_out))

    assert str(raised.value).endswith("failed: ReadTimeout")
