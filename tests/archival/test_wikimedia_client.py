"""Wikimedia Commons client against a trimmed live response (httpx.MockTransport).

``tests/fixtures/archival/wikimedia_search.json`` holds six real File pages from
``commons.wikimedia.org/w/api.php`` (generator=search, formatversion=2,
prop=imageinfo with extmetadata) captured on 2026-09-30. Cases the live data
does not contain are built by editing a copy of one page.
"""

import copy

import httpx
import pytest

from hyperframes_mcp.archival import (
    ArchivalProviderError,
    RightsStatus,
    WikimediaCommonsClient,
)
from tests.archival.helpers import mock_client
from tests.archival.oracle import load_fixture

PD_PAGE, BY_PAGE, SA_PAGE, _, CC0_PAGE, NASA_PAGE = range(6)


def _client(payload=None, **kwargs):
    payload = load_fixture("wikimedia_search.json") if payload is None else payload
    return mock_client(WikimediaCommonsClient, payload, **kwargs)


def _page(index=PD_PAGE, **ext_overrides):
    """A deep copy of one live page with extmetadata fields overridden or removed.

    A value of ``None`` removes the field.
    """
    page = copy.deepcopy(load_fixture("wikimedia_search.json")["query"]["pages"][index])
    extmetadata = page["imageinfo"][0]["extmetadata"]
    for field, value in ext_overrides.items():
        if value is None:
            extmetadata.pop(field, None)
        else:
            extmetadata[field] = {"value": value, "source": "test"}
    return page


def _payload(*pages):
    return {"batchcomplete": True, "query": {"pages": list(pages)}}


async def _one(page):
    (candidate,) = await _client(_payload(page)).search("q")
    return candidate


@pytest.mark.asyncio
async def test_search_returns_all_pages_in_search_order():
    candidates = await _client().search("standard oil")

    assert [c.provider_id for c in candidates] == [
        "163834389",
        "3413544",
        "75555727",
        "183372324",
        "102629154",
        "102412",
    ]


@pytest.mark.asyncio
async def test_public_domain_page_is_public_domain_with_fields():
    pd = (await _client().search("q"))[PD_PAGE]

    assert pd.rights_status is RightsStatus.PUBLIC_DOMAIN
    assert pd.needs_review is False
    assert pd.usable_by_default is True
    assert pd.title == "Standard Oil Refinery Richmond CA 1913"
    assert pd.creator == "West Coast Art Co."
    assert pd.date == "c. 1913"
    assert pd.source_url == ("https://commons.wikimedia.org/wiki/File:Standard_Oil_Refinery_Richmond_CA_1913.jpg")
    assert pd.direct_file_url.startswith(
        "https://upload.wikimedia.org/wikipedia/commons/7/7c/Standard_Oil_Refinery_Richmond_CA_1913.jpg"
    )
    assert (pd.dimensions.width, pd.dimensions.height) == (2905, 700)
    assert pd.provenance.raw_rights_fields["License"] == "pd"
    assert pd.provenance.raw_rights_fields["Copyrighted"] == "False"


@pytest.mark.asyncio
async def test_cc_by_needs_attribution_and_html_artist_is_cleaned():
    by = (await _client().search("q"))[BY_PAGE]

    assert by.rights_status is RightsStatus.ATTRIBUTION_REQUIRED
    assert by.licence_id == "CC-BY-2.5"
    assert by.licence_url == "https://creativecommons.org/licenses/by/2.5"
    assert by.creator == "Walter Siegmund"  # "(talk)" signature and link markup removed
    assert by.usable_by_default is True
    assert "Licensed under CC-BY-2.5" in by.attribution_text
    assert "Walter Siegmund" in by.attribution_text
    assert by.provenance.raw_rights_fields["AttributionRequired"] == "true"


@pytest.mark.asyncio
async def test_text_hidden_inside_the_artist_is_not_read_as_part_of_the_name():
    # the live Artist field of NARA 5757186: Commons hides a copy of "Unknown author" inside the visible words
    artist = (
        '<div class="fn value">\nUnknown author<span style="display: none;">Unknown author</span> or not provided</div>'
    )

    candidate = await _one(_page(PD_PAGE, Artist=artist))

    assert candidate.creator == "Unknown author or not provided"


@pytest.mark.asyncio
async def test_hidden_text_with_markup_inside_it_is_left_out_to_its_closing_tag():
    artist = 'Ann <span style="display:none">gone <span>nested</span> too</span><b>Bell</b>'

    candidate = await _one(_page(PD_PAGE, Artist=artist))

    assert candidate.creator == "Ann Bell"


@pytest.mark.asyncio
async def test_cc_by_sa_is_share_alike_and_not_for_default_use():
    sa = (await _client().search("q"))[SA_PAGE]

    assert sa.rights_status is RightsStatus.SHARE_ALIKE
    assert sa.licence_id == "CC-BY-SA-4.0"
    assert sa.needs_review is False
    assert sa.usable_by_default is False


@pytest.mark.asyncio
async def test_cc0_page_is_cc0():
    cc0 = (await _client().search("q"))[CC0_PAGE]

    assert cc0.rights_status is RightsStatus.CC0
    assert cc0.licence_id == "CC0-1.0"
    assert cc0.provenance.raw_rights_fields["License"] == "cc0"


@pytest.mark.asyncio
async def test_us_government_photo_tagged_pd_by_commons_is_public_domain():
    nasa = (await _client().search("q"))[NASA_PAGE]

    assert nasa.creator == "NASA"
    assert nasa.rights_status is RightsStatus.PUBLIC_DOMAIN


@pytest.mark.asyncio
async def test_non_empty_restrictions_keep_the_status_but_flag_review():
    candidate = await _one(_page(PD_PAGE, Restrictions="trademarked|personality"))

    assert candidate.rights_status is RightsStatus.PUBLIC_DOMAIN
    assert candidate.needs_review is True
    assert candidate.usable_by_default is False
    assert candidate.review_reasons == ("Commons restrictions: trademarked|personality",)


@pytest.mark.asyncio
async def test_public_domain_tag_on_a_file_commons_marks_copyrighted_is_unknown():
    candidate = await _one(_page(PD_PAGE, Copyrighted="True"))

    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
async def test_missing_licence_fields_are_unknown_not_inferred_from_age():
    page = _page(
        PD_PAGE,
        License=None,
        LicenseShortName=None,
        UsageTerms=None,
        AttributionRequired=None,
        Copyrighted=None,
    )

    candidate = await _one(page)

    assert candidate.date == "c. 1913"  # old, but that is never a rights signal
    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
async def test_unrecognised_licence_name_is_unknown():
    candidate = await _one(_page(PD_PAGE, License="pd", LicenseShortName="Free Art License"))

    assert candidate.rights_status is RightsStatus.UNKNOWN


@pytest.mark.asyncio
async def test_non_commercial_licence_is_restricted():
    page = _page(
        BY_PAGE,
        License="cc-by-nc-4.0",
        LicenseShortName="CC BY-NC 4.0",
        LicenseUrl="https://creativecommons.org/licenses/by-nc/4.0",
    )

    candidate = await _one(page)

    assert candidate.rights_status is RightsStatus.RESTRICTED
    assert candidate.usable_by_default is False


@pytest.mark.asyncio
async def test_attribution_item_without_a_creator_needs_review():
    candidate = await _one(_page(BY_PAGE, Artist=None))

    assert candidate.rights_status is RightsStatus.ATTRIBUTION_REQUIRED
    assert candidate.needs_review is True
    assert "no creator" in candidate.review_reasons[0]
    assert candidate.usable_by_default is False


@pytest.mark.asyncio
async def test_required_attribution_text_from_the_rights_holder_is_used_verbatim():
    candidate = await _one(_page(BY_PAGE, Attribution="Photo: W. Siegmund for Example Press"))

    assert candidate.attribution_text.startswith("Photo: W. Siegmund for Example Press. Source:")


@pytest.mark.asyncio
async def test_svg_and_non_https_files_are_skipped():
    svg = _page(PD_PAGE)
    svg["imageinfo"][0]["mime"] = "image/svg+xml"
    insecure = _page(BY_PAGE)
    insecure["imageinfo"][0]["url"] = "http://upload.wikimedia.org/x.jpg"

    assert await _client(_payload(svg, insecure)).search("q") == []


@pytest.mark.asyncio
async def test_no_matches_returns_empty_list():
    assert await _client({"batchcomplete": True}).search("zzzzqqqq") == []


@pytest.mark.asyncio
async def test_request_asks_for_extmetadata_and_identifies_itself():
    requests: list[httpx.Request] = []
    await _client(requests=requests).search("standard oil", limit=4)

    (request,) = requests
    params = request.url.params
    assert request.url.host == "commons.wikimedia.org"
    assert request.url.path == "/w/api.php"
    assert params["action"] == "query"
    assert params["formatversion"] == "2"
    assert params["gsrsearch"] == "filetype:bitmap standard oil"
    assert params["gsrnamespace"] == "6"
    assert params["gsrlimit"] == "4"
    assert params["iiprop"] == "url|size|mime|extmetadata"
    for field in ("LicenseShortName", "LicenseUrl", "Artist", "Credit", "Restrictions"):
        assert field in params["iiextmetadatafilter"].split("|")
    assert "hyperframes-mcp" in request.headers["user-agent"]
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_custom_user_agent_is_sent():
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"batchcomplete": True})

    client = WikimediaCommonsClient(
        user_agent="hyperframes-mcp/1.0 (mailto:ops@example.org)",
        transport=httpx.MockTransport(handler),
    )
    await client.search("q")

    assert requests[0].headers["user-agent"] == "hyperframes-mcp/1.0 (mailto:ops@example.org)"


@pytest.mark.asyncio
async def test_provenance_records_api_url_and_time():
    candidate = (await _client().search("q"))[BY_PAGE]

    assert candidate.provenance.retrieved_at.tzinfo is not None
    assert candidate.provenance.api_url.startswith("https://commons.wikimedia.org/w/api.php?action=query")


@pytest.mark.asyncio
async def test_http_error_and_bad_shapes_raise_provider_error():
    with pytest.raises(ArchivalProviderError):
        await _client(status=503).search("q")
    with pytest.raises(ArchivalProviderError):
        await _client(content=b"not json").search("q")
    with pytest.raises(ArchivalProviderError):
        await _client({"query": {"pages": "nope"}}).search("q")
    with pytest.raises(ArchivalProviderError):
        await _client(["a list"]).search("q")


# Commons search ANDs every word, so a long cue query often finds nothing. The client then retries
# once with the first half of the words (the cue's subject comes first, place and year last).


def _by_search(answers, requests):
    """A client answering by its ``gsrsearch``; a search not in ``answers`` finds nothing."""

    def handler(request):
        search = request.url.params["gsrsearch"].removeprefix("filetype:bitmap ")
        requests.append(search)
        return httpx.Response(200, json=answers.get(search, {"batchcomplete": True}))

    return WikimediaCommonsClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_long_query_that_finds_nothing_is_retried_with_its_first_half():
    searches: list[str] = []
    client = _by_search({"gold rush": load_fixture("wikimedia_search.json")}, searches)

    candidates = await client.search("gold rush miners california")

    assert len(candidates) == 6
    assert searches == ["gold rush miners california", "gold rush"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "retried_with"),
    [
        ("gold rush", "gold"),
        ("gold rush miners", "gold rush"),
        ("gold rush miners california", "gold rush"),
        ("gold rush miners california 1849", "gold rush miners"),
        ("gold rush miners california sutter 1849", "gold rush miners"),
    ],
)
async def test_the_retry_keeps_the_first_ceil_half_of_the_words(query, retried_with):
    searches: list[str] = []

    assert await _by_search({}, searches).search(query) == []

    assert searches == [query, retried_with]


@pytest.mark.asyncio
async def test_no_retry_when_the_first_search_found_something():
    searches: list[str] = []
    client = _by_search({"gold rush miners": load_fixture("wikimedia_search.json")}, searches)

    assert len(await client.search("gold rush miners")) == 6
    assert searches == ["gold rush miners"]


@pytest.mark.asyncio
async def test_a_one_word_query_is_never_retried():
    searches: list[str] = []

    assert await _by_search({}, searches).search("gold") == []

    assert searches == ["gold"]


@pytest.mark.asyncio
async def test_a_failed_retry_raises_instead_of_looking_like_no_results():
    def handler(request):
        if "miners" in request.url.params["gsrsearch"]:
            return httpx.Response(200, json={"batchcomplete": True})
        return httpx.Response(404)

    client = WikimediaCommonsClient(transport=httpx.MockTransport(handler))

    with pytest.raises(ArchivalProviderError):
        await client.search("gold rush miners california")
