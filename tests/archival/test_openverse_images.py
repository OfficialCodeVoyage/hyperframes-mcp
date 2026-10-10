"""Openverse image search as an archival provider (httpx.MockTransport).

``tests/fixtures/archival/openverse_images_search.json`` is shaped like ``api.openverse.org/v1/images/`` answers: its third
item is a real one (a Flickr CC BY picture, captured 2026-10-07, tags trimmed) and the others are written to the same
shape, with the cases the live data rarely has: a picture on a plain-http host, no size, a share-alike licence, a licence
and a deed that disagree, and an item with no id.
"""

import httpx
import pytest

from hyperframes_mcp.archival import (
    DEFAULT_USER_AGENT,
    ArchivalProvider,
    ArchivalProviderError,
    RightsStatus,
)
from hyperframes_mcp.archival.openverse import OpenverseImageClient
from tests.archival.helpers import mock_client
from tests.archival.oracle import load_fixture

(
    CC0_COIN,
    PDM_STREET,
    BY_VAULT,
    BY_NO_AUTHOR,
    HTTP_ONLY,
    NO_SIZE,
    SHARE_ALIKE,
    MISMATCH,
) = range(8)


def _client(payload=None, **kwargs):
    payload = load_fixture("openverse_images_search.json") if payload is None else payload
    return mock_client(OpenverseImageClient, payload, **kwargs)


@pytest.mark.asyncio
async def test_it_asks_for_cc0_the_public_domain_mark_and_cc_by_anonymously_with_a_user_agent():
    seen: list[httpx.Request] = []

    await _client(requests=seen).search("vault door", limit=6)

    (request,) = seen
    assert request.url.host == "api.openverse.org"
    assert request.url.path == "/v1/images/"
    assert dict(request.url.params) == {
        "q": "vault door",
        "license": "cc0,pdm,by",
        "page_size": "6",
    }
    assert request.headers["user-agent"] == DEFAULT_USER_AGENT
    assert "authorization" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "asked"), [(0, "1"), (5, "5"), (50, "20")])
async def test_a_page_is_at_least_one_and_at_most_twenty_the_most_openverse_gives_anonymously(limit, asked):
    seen: list[httpx.Request] = []

    await _client(requests=seen).search("x", limit=limit)

    assert seen[0].url.params["page_size"] == asked


@pytest.mark.asyncio
async def test_every_item_with_an_id_is_a_candidate_in_openverses_order():
    candidates = await _client().search("q")

    assert [c.provider_id for c in candidates] == [
        r["id"] for r in load_fixture("openverse_images_search.json")["results"] if "id" in r
    ]
    assert {c.provider for c in candidates} == {"openverse"}


@pytest.mark.asyncio
async def test_a_cc0_museum_item_is_cc0_with_its_file_size_and_landing_page_and_its_tags_as_words_to_match():
    coin = (await _client().search("q"))[CC0_COIN]

    assert coin.rights_status is RightsStatus.CC0
    assert coin.licence_id == "CC0-1.0" and coin.usable_by_default
    assert coin.title == "Tetradrachm of Athens, silver coin"
    assert coin.source_url == "https://www.metmuseum.org/art/collection/search/245161"
    assert coin.direct_file_url == "https://images.metmuseum.org/CRDImages/gr/original/DP117421.jpg"
    assert (coin.dimensions.width, coin.dimensions.height) == (3000, 2000)
    assert coin.description == "coin, silver, athens"
    assert coin.provenance.raw_rights_fields["license"] == "cc0"
    assert "Source: Openverse, https://www.metmuseum.org" in coin.attribution_text
    assert coin.attribution_text.endswith("CC0 1.0 public domain dedication.")


@pytest.mark.asyncio
async def test_the_public_domain_mark_is_public_domain_and_credits_the_library():
    street = (await _client().search("q"))[PDM_STREET]

    assert street.rights_status is RightsStatus.PUBLIC_DOMAIN
    assert street.usable_by_default
    assert street.creator == "The New York Public Library"
    assert street.attribution_text.startswith('"Wall Street, 1929" by The New York Public Library.')


@pytest.mark.asyncio
async def test_cc_by_needs_its_creator_credited_and_one_without_a_creator_is_held_for_review():
    candidates = await _client().search("q")
    vault, no_author = candidates[BY_VAULT], candidates[BY_NO_AUTHOR]

    assert vault.rights_status is RightsStatus.ATTRIBUTION_REQUIRED
    assert vault.usable_by_default
    assert "Infrogmation" in vault.attribution_text
    assert "Licensed under CC-BY-2.0" in vault.attribution_text
    assert no_author.rights_status is RightsStatus.ATTRIBUTION_REQUIRED
    assert no_author.needs_review and not no_author.usable_by_default
    assert "no creator" in no_author.review_reasons[-1]


@pytest.mark.asyncio
async def test_only_an_https_file_is_a_direct_file_and_an_unstated_size_is_none():
    candidates = await _client().search("q")

    assert candidates[HTTP_ONLY].direct_file_url is None
    assert candidates[HTTP_ONLY].source_url == "https://example.org/pictures/5"
    assert candidates[NO_SIZE].dimensions is None
    assert candidates[NO_SIZE].description is None


@pytest.mark.asyncio
async def test_a_share_alike_item_and_one_whose_licence_and_deed_disagree_are_never_usable():
    candidates = await _client().search("q")
    share_alike, mismatch = candidates[SHARE_ALIKE], candidates[MISMATCH]

    assert share_alike.rights_status is RightsStatus.SHARE_ALIKE
    assert not share_alike.usable_by_default
    assert mismatch.rights_status is RightsStatus.UNKNOWN
    assert mismatch.needs_review and not mismatch.usable_by_default


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "complaint"),
    [
        ({"status": 500, "payload": {}}, "500"),
        ({"payload": {"result_count": 0}}, "no results list"),
        ({"payload": []}, "no results list"),
        ({"content": b"<html>not json</html>"}, "invalid JSON"),
    ],
)
async def test_an_error_or_an_answer_of_the_wrong_shape_is_a_provider_error_the_caller_can_report(kwargs, complaint):
    kwargs = dict(kwargs)
    payload = kwargs.pop("payload", None)
    client = _client(payload if payload is not None else {}, **kwargs)

    with pytest.raises(ArchivalProviderError, match=complaint):
        await client.search("q")


@pytest.mark.asyncio
async def test_it_is_an_archival_provider_that_closes():
    client = OpenverseImageClient()

    assert isinstance(client, ArchivalProvider) and client.name == "openverse"
    await client.close()
