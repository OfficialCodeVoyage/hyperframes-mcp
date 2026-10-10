"""Library of Congress client against a trimmed live response (httpx.MockTransport).

``tests/fixtures/archival/loc_search.json`` holds five real results from
``https://www.loc.gov/photos/?fo=json&fa=online-format:image`` captured on
2026-09-30 and trimmed to the fields the client reads. Cases the live data does
not contain (an explicit public-domain statement) are built by editing a copy.
"""

import copy

import httpx
import pytest

from hyperframes_mcp.archival import (
    ArchivalProviderError,
    LibraryOfCongressClient,
    RightsStatus,
)
from tests.archival.helpers import mock_client
from tests.archival.oracle import load_fixture

LIVE_FIXTURE_IDS = ["2025662344", "2004679630", "18015276", "2010630382"]


def _client(payload=None, **kwargs):
    payload = load_fixture("loc_search.json") if payload is None else payload
    return mock_client(LibraryOfCongressClient, payload, **kwargs)


def _result_with_rights(rights_text, *, access_restricted=False, **item_overrides):
    """The postcard result with its rights statement replaced."""
    payload = load_fixture("loc_search.json")
    result = copy.deepcopy(payload["results"][1])
    result["access_restricted"] = access_restricted
    result["item"]["rights_advisory"] = rights_text
    result["item"]["rights_information"] = rights_text
    result["item"].update(item_overrides)
    return {"results": [result]}


@pytest.mark.asyncio
async def test_search_returns_only_results_with_a_real_image():
    candidates = await _client().search("standard oil")

    # The first live result is a group of images with a placeholder icon.
    assert [c.provider_id for c in candidates] == LIVE_FIXTURE_IDS


@pytest.mark.asyncio
async def test_postcard_fields_and_largest_derivative():
    postcard = (await _client().search("standard oil"))[0]

    assert postcard.provider == "loc"
    assert postcard.source_url == "https://www.loc.gov/item/2025662344/"
    assert postcard.direct_file_url == (
        "https://tile.loc.gov/storage-services/service/pnp/pcrd/1a00000/1a00000/1a00700/1a00713v.jpg"
    )
    assert (postcard.dimensions.width, postcard.dimensions.height) == (1024, 680)
    assert postcard.title == "Standard Oil Refinery, near Los Angeles Harbor, Calif."
    assert postcard.date == "1914"
    assert postcard.creator is None
    assert postcard.rights_statement == "No known restrictions on publication."


@pytest.mark.asyncio
async def test_main_creator_is_credited_not_added_entries():
    candidates = {c.provider_id: c for c in await _client().search("q")}

    assert candidates["18015276"].creator == "Jackson, Joseph, 1867-1946."
    assert candidates["2010630382"].creator == "Highsmith, Carol M., 1946-"


@pytest.mark.asyncio
async def test_iiif_url_without_a_stated_size_is_still_chosen_when_it_is_the_largest_listed():
    candidates = {c.provider_id: c for c in await _client().search("q")}
    book = candidates["18015276"]

    assert book.direct_file_url.endswith("/full/pct:100.0/0/default.jpg")
    assert (book.dimensions.width, book.dimensions.height) == (1856, 2734)


@pytest.mark.asyncio
async def test_live_results_are_unknown_and_flagged_never_public_domain():
    """The live "No known restrictions" and "not evaluated" wording is not a PD statement."""
    candidates = await _client().search("q")

    assert candidates
    for candidate in candidates:
        assert candidate.rights_status is RightsStatus.UNKNOWN, candidate.provider_id
        assert candidate.needs_review is True
        assert candidate.usable_by_default is False


@pytest.mark.asyncio
async def test_item_with_no_rights_fields_is_unknown_with_a_reason():
    book = next(c for c in await _client().search("q") if c.provider_id == "18015276")

    assert book.rights_status is RightsStatus.UNKNOWN
    assert book.rights_statement is None
    assert "no rights statement" in book.review_reasons[0]


@pytest.mark.asyncio
async def test_old_date_alone_never_makes_an_item_public_domain():
    result = copy.deepcopy(load_fixture("loc_search.json")["results"][1])
    result["date"] = "1850"
    result["item"].pop("rights_advisory")
    result["item"].pop("rights_information")

    candidate = (await _client({"results": [result]}).search("q"))[0]

    assert candidate.date == "1850"
    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    ["Public domain.", "Public domain (U.S. Government work)."],
)
async def test_explicit_public_domain_statement_from_loc_is_public_domain(statement):
    candidate = (await _client(_result_with_rights(statement)).search("q"))[0]

    assert candidate.rights_status is RightsStatus.PUBLIC_DOMAIN
    assert candidate.needs_review is False
    assert candidate.usable_by_default is True
    assert candidate.provenance.raw_rights_fields["rights_advisory"] == statement
    assert candidate.attribution_text.endswith("Public domain.")


@pytest.mark.asyncio
async def test_public_domain_statement_on_an_access_restricted_item_is_unknown():
    candidate = (await _client(_result_with_rights("Public domain.", access_restricted=True)).search("q"))[0]

    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
async def test_stated_restriction_is_restricted():
    candidate = (await _client(_result_with_rights("All rights reserved.")).search("q"))[0]

    assert candidate.rights_status is RightsStatus.RESTRICTED
    assert candidate.usable_by_default is False


@pytest.mark.asyncio
async def test_non_item_pages_are_skipped():
    payload = load_fixture("loc_search.json")
    blog = copy.deepcopy(payload["results"][1])
    blog["url"] = "https://blogs.loc.gov/inside_adams/2021/03/united-copper-panic-of-1907/"

    assert await _client({"results": [blog]}).search("q") == []


@pytest.mark.asyncio
async def test_non_https_image_urls_are_skipped():
    payload = load_fixture("loc_search.json")
    result = copy.deepcopy(payload["results"][1])
    result["image_url"] = ["http://tile.loc.gov/x/y.jpg#h=100&w=100"]

    assert await _client({"results": [result]}).search("q") == []


@pytest.mark.asyncio
async def test_request_is_polite_unauthenticated_and_scoped_to_images():
    requests: list[httpx.Request] = []
    await _client(requests=requests).search("standard oil", limit=7)

    (request,) = requests
    assert request.url.host == "www.loc.gov"
    assert request.url.path == "/photos/"
    assert dict(request.url.params) == {
        "q": "standard oil",
        "fo": "json",
        "c": "7",
        "fa": "online-format:image",
    }
    assert "hyperframes-mcp" in request.headers["user-agent"]
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_provenance_records_when_where_and_the_raw_rights_fields():
    postcard = (await _client().search("q"))[0]

    provenance = postcard.provenance
    assert provenance.retrieved_at.tzinfo is not None
    assert provenance.api_url.startswith("https://www.loc.gov/photos/?")
    assert "fo=json" in provenance.api_url
    assert provenance.raw_rights_fields["rights_advisory"] == ("No known restrictions on publication.")
    assert provenance.raw_rights_fields["rights_information"] == ("No known restrictions on publication.")


@pytest.mark.asyncio
async def test_http_error_raises_provider_error():
    with pytest.raises(ArchivalProviderError):
        await _client(status=500).search("q")


@pytest.mark.asyncio
async def test_invalid_json_raises_provider_error():
    with pytest.raises(ArchivalProviderError):
        await _client(content=b"<html>not json</html>").search("q")


@pytest.mark.asyncio
async def test_unexpected_shape_raises_provider_error():
    with pytest.raises(ArchivalProviderError):
        await _client({"unexpected": []}).search("q")
