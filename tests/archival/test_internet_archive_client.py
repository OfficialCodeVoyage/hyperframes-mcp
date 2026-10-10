"""Internet Archive client against trimmed live responses (httpx.MockTransport).

The fixtures are real ``advancedsearch.php`` and ``/metadata/{identifier}``
responses captured on 2026-09-30: ``cmlpl_000770`` (Public Domain Mark URL and a
consistent public-domain note), ``cscrm_001416`` (Public Domain Mark URL but a
rights note saying "Copyright status unknown", access-restricted) and
``Smittys010`` (no licenseurl; a note that commercial use needs a fee).
``grand-trunk-1936`` has no metadata fixture, so its metadata request is a 404.
Cases the live data does not contain are built with ``_metadata``.
"""

import httpx
import pytest

from hyperframes_mcp.archival import (
    ArchivalProviderError,
    InternetArchiveClient,
    RightsStatus,
)
from tests.archival.helpers import BY
from tests.archival.oracle import CC0_URL, PDM_URL, fixture_handler


def _fixture_client(requests=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return fixture_handler(request)

    return InternetArchiveClient(transport=httpx.MockTransport(handler))


def _metadata(identifier="item1", *, files=None, **meta):
    if files is None:
        files = [
            {
                "name": f"{identifier}.jpg",
                "source": "original",
                "format": "JPEG",
                "size": "1000",
            }
        ]
    return {
        "files": files,
        "metadata": {
            "identifier": identifier,
            "title": "A title",
            "mediatype": "image",
            **meta,
        },
    }


def _synthetic_client(*responses, requests=None, metadata_status=200):
    """Search returns the given identifiers; each metadata call returns its response."""
    by_id = {r["metadata"]["identifier"]: r for r in responses}

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/advancedsearch.php":
            docs = [{"identifier": i} for i in by_id]
            return httpx.Response(200, json={"response": {"numFound": len(docs), "docs": docs}})
        identifier = request.url.path.rsplit("/", 1)[1]
        if metadata_status != 200:
            return httpx.Response(metadata_status)
        return httpx.Response(200, json=by_id[identifier])

    return InternetArchiveClient(transport=httpx.MockTransport(handler))


async def _one(response):
    (candidate,) = await _synthetic_client(response).search("q")
    return candidate


@pytest.mark.asyncio
async def test_search_skips_items_whose_metadata_cannot_be_fetched():
    candidates = await _fixture_client().search("standard oil")

    assert [c.provider_id for c in candidates] == [
        "cmlpl_000770",
        "cscrm_001416",
        "Smittys010",
    ]


@pytest.mark.asyncio
async def test_pdm_item_with_a_consistent_note_is_public_domain():
    pd = (await _fixture_client().search("q"))[0]

    assert pd.rights_status is RightsStatus.PUBLIC_DOMAIN
    assert pd.needs_review is False
    assert pd.licence_id == "PDM-1.0"
    assert pd.licence_url == PDM_URL
    assert pd.source_url == "https://archive.org/details/cmlpl_000770"
    assert pd.direct_file_url == "https://archive.org/download/cmlpl_000770/cmlpl_000770_access.jpg"
    assert pd.title.startswith("Mount Tamalpais and Muir Woods")
    assert pd.creator is None  # the Archive says "Unknown"
    assert pd.date == "1920"
    assert pd.description and "<" not in pd.description  # HTML stripped
    assert pd.provenance.raw_rights_fields["licenseurl"] == PDM_URL
    assert pd.provenance.raw_rights_fields["rights"] == "Public Domain. No restrictions on use."
    assert pd.provenance.api_url == "https://archive.org/metadata/cmlpl_000770"


@pytest.mark.asyncio
async def test_pdm_url_contradicted_by_the_rights_note_is_unknown():
    gold_bar = (await _fixture_client().search("q"))[1]

    assert gold_bar.provider_id == "cscrm_001416"
    assert gold_bar.licence_id == "PDM-1.0"
    assert gold_bar.rights_status is RightsStatus.UNKNOWN
    assert gold_bar.needs_review is True
    assert gold_bar.usable_by_default is False
    assert "Copyright status unknown" in gold_bar.rights_statement
    assert gold_bar.provenance.raw_rights_fields["access-restricted-item"] == "true"


@pytest.mark.asyncio
async def test_access_restricted_item_has_no_direct_download_url():
    gold_bar = (await _fixture_client().search("q"))[1]

    assert gold_bar.direct_file_url is None
    assert gold_bar.date is None  # "XXXX" placeholder dropped


@pytest.mark.asyncio
async def test_fee_note_without_a_licence_url_is_restricted():
    smittys = (await _fixture_client().search("q"))[2]

    assert smittys.provider_id == "Smittys010"
    assert smittys.rights_status is RightsStatus.RESTRICTED
    assert smittys.direct_file_url == "https://archive.org/download/Smittys010/Smittys010.jpg"


@pytest.mark.asyncio
async def test_old_date_and_copyright_status_field_never_make_an_item_public_domain():
    candidate = await _one(
        _metadata(
            **{
                "date": "1850",
                "possible-copyright-status": "NOT_IN_COPYRIGHT",
                "copyright-region": "US",
            }
        )
    )

    assert candidate.date == "1850"
    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
async def test_public_domain_note_without_a_licence_url_is_unknown():
    candidate = await _one(_metadata(rights="Public Domain. No restrictions on use."))

    assert candidate.rights_status is RightsStatus.UNKNOWN
    assert candidate.needs_review


@pytest.mark.asyncio
async def test_creative_commons_licence_urls_map_to_their_status():
    cases = {
        CC0_URL: RightsStatus.CC0,
        "https://creativecommons.org/licenses/by/4.0/": RightsStatus.ATTRIBUTION_REQUIRED,
        "https://creativecommons.org/licenses/by-sa/4.0/": RightsStatus.SHARE_ALIKE,
        "https://creativecommons.org/licenses/by-nc/4.0/": RightsStatus.RESTRICTED,
    }
    for url, status in cases.items():
        candidate = await _one(_metadata(licenseurl=url))
        assert candidate.rights_status is status, url


@pytest.mark.asyncio
async def test_unrecognised_or_list_licence_url_is_unknown():
    assert (await _one(_metadata(licenseurl="https://example.com/pd"))).rights_status is (RightsStatus.UNKNOWN)
    both = [PDM_URL, CC0_URL]
    assert (await _one(_metadata(licenseurl=both))).rights_status is RightsStatus.UNKNOWN


@pytest.mark.asyncio
async def test_prefers_web_formats_over_tiff_and_picks_the_largest():
    files = [
        {"name": "a.tif", "source": "original", "format": "TIFF", "size": "90000000"},
        {"name": "small.jpg", "source": "original", "format": "JPEG", "size": "100"},
        {
            "name": "big image.jpg",
            "source": "original",
            "format": "JPEG",
            "size": "5000",
        },
        {
            "name": "thumb.jpg",
            "source": "derivative",
            "format": "JPEG Thumb",
            "size": "9999",
        },
        {
            "name": "tile.jpg",
            "source": "original",
            "format": "Item Tile",
            "size": "99999",
        },
    ]
    candidate = await _one(_metadata("my item", files=files, licenseurl=PDM_URL))

    assert candidate.direct_file_url == "https://archive.org/download/my%20item/big%20image.jpg"


@pytest.mark.asyncio
async def test_tiff_is_used_when_it_is_the_only_image():
    files = [{"name": "a.tif", "source": "original", "format": "TIFF", "size": "10"}]

    assert (await _one(_metadata(files=files))).direct_file_url.endswith("/a.tif")


@pytest.mark.asyncio
async def test_item_without_an_image_file_is_skipped():
    files = [{"name": "meta.xml", "source": "original", "format": "Metadata"}]

    assert await _synthetic_client(_metadata(files=files)).search("q") == []


@pytest.mark.asyncio
async def test_stated_dimensions_are_used_when_the_file_lists_them():
    files = [
        {
            "name": "p.jpg",
            "source": "original",
            "format": "JPEG",
            "size": "1",
            "width": "800",
            "height": "600",
        }
    ]
    candidate = await _one(_metadata(files=files))

    assert (candidate.dimensions.width, candidate.dimensions.height) == (800, 600)


@pytest.mark.asyncio
async def test_search_then_one_metadata_request_per_hit_with_polite_headers():
    requests: list[httpx.Request] = []
    await _fixture_client(requests).search("standard oil", limit=4)

    search, *metadata = requests
    assert search.url.path == "/advancedsearch.php"
    assert search.url.params["q"] == "(standard oil) AND mediatype:image"
    assert search.url.params["rows"] == "4"
    assert search.url.params["output"] == "json"
    assert sorted(r.url.path for r in metadata) == [
        "/metadata/Smittys010",
        "/metadata/cmlpl_000770",
        "/metadata/cscrm_001416",
        "/metadata/grand-trunk-1936",
    ]
    assert all("hyperframes-mcp" in r.headers["user-agent"] for r in requests)
    assert all("authorization" not in r.headers for r in requests)


@pytest.mark.asyncio
async def test_metadata_server_error_skips_the_item_but_search_failure_raises():
    assert await _synthetic_client(_metadata(), metadata_status=500).search("q") == []

    failing = InternetArchiveClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(ArchivalProviderError):
        await failing.search("q")


@pytest.mark.asyncio
async def test_unexpected_search_shape_raises_provider_error():
    client = InternetArchiveClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(ArchivalProviderError):
        await client.search("q")


@pytest.mark.asyncio
async def test_credit_required_item_without_a_creator_needs_review():
    candidate = await _one(_metadata(licenseurl="https://creativecommons.org/licenses/by/4.0/", creator="Unknown"))

    assert candidate.rights_status is BY
    assert candidate.creator is None
    assert candidate.needs_review is True
    assert candidate.usable_by_default is False
    assert "no creator" in candidate.review_reasons[-1]
