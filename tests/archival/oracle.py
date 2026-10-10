"""Expected rights outcomes for the archival package: the test oracle.

``check_rights_cases`` and ``check_fixture_statuses`` raise ``AssertionError``
when the rights mapping drifts from the tables below. The mutation tests in
``test_rights_mutations.py`` run them against deliberately broken mappings and
require them to fail, which proves these tables guard the mapping.

The rights module is looked up at call time (``rights.assess_loc`` and so on)
so a patched function or table is what gets checked.
"""

import json
from pathlib import Path
from typing import Any

import httpx

from hyperframes_mcp.archival import (
    ArchivalCandidate,
    InternetArchiveClient,
    LibraryOfCongressClient,
    RightsStatus,
    WikimediaCommonsClient,
    rights,
)
from tests.archival.helpers import BY, CC0, PD, RESTRICTED, SA, UNKNOWN

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "archival"

PDM_URL = "http://creativecommons.org/publicdomain/mark/1.0/"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
COPYRIGHT_UNKNOWN_TEXT = (
    "Copyright status unknown. This work may be protected by the U.S. Copyright Law (Title 17, U.S.C.)."
)

# (id, rights texts, access_restricted, expected status, expected needs_review)
LOC_CASES: list[tuple[str, list[str | None], bool, RightsStatus, bool]] = [
    (
        "no-known-restrictions",
        ["No known restrictions on publication."],
        False,
        UNKNOWN,
        True,
    ),
    (
        "not-evaluated",
        ['Rights status not evaluated. For general information see: "Copyright and Other Restrictions ..."'],
        False,
        UNKNOWN,
        True,
    ),
    ("no-statement", [], False, UNKNOWN, True),
    ("blank-statement", [None, "   "], False, UNKNOWN, True),
    ("public-domain", ["Public domain."], False, PD, False),
    (
        "us-gov-work-parenthetical",
        ["Public domain (U.S. Government work)."],
        False,
        PD,
        False,
    ),
    ("us-gov-work-alone", ["U.S. Government work"], False, PD, False),
    (
        "work-of-us-government",
        ["This is a work of the United States Government."],
        False,
        PD,
        False,
    ),
    (
        "gov-employee",
        ["Created by an employee of the U.S. government."],
        False,
        PD,
        False,
    ),
    (
        "doubt-beats-public-domain",
        ["Public domain.", "No known restrictions on publication."],
        False,
        UNKNOWN,
        True,
    ),
    ("access-restricted-blocks-pd", ["Public domain."], True, UNKNOWN, True),
    ("negated-public-domain", ["Not in the public domain."], False, UNKNOWN, True),
    (
        "negated-gov-work",
        ["This item is not a U.S. Government work."],
        False,
        UNKNOWN,
        True,
    ),
    (
        "may-be-restricted",
        ["Publication may be restricted. For information see: https://example.org"],
        False,
        UNKNOWN,
        True,
    ),
    (
        "probably-public-domain",
        ["Public domain in the United States, probably."],
        False,
        UNKNOWN,
        True,
    ),
    (
        "believed-public-domain",
        ["Believed to be in the public domain."],
        False,
        UNKNOWN,
        True,
    ),
    ("age-alone", ["Published in 1895; copyright has expired."], False, UNKNOWN, True),
    (
        "age-alone-2",
        ["Out of copyright because it was published before 1930."],
        False,
        UNKNOWN,
        True,
    ),
    ("vague-free", ["Free to use."], False, UNKNOWN, True),
    ("all-rights-reserved", ["All rights reserved."], False, RESTRICTED, False),
    ("copyright-claim", ["Copyright 1950 by John Doe."], False, RESTRICTED, False),
    (
        "permission-required",
        ["Permission required for commercial use."],
        False,
        RESTRICTED,
        False,
    ),
]

# (id, flattened extmetadata, expected status, expected needs_review)
WIKIMEDIA_CASES: list[tuple[str, dict[str, str], RightsStatus, bool]] = [
    (
        "pd-template",
        {
            "License": "pd",
            "LicenseShortName": "Public domain",
            "UsageTerms": "Public domain",
            "AttributionRequired": "false",
            "Copyrighted": "False",
            "Restrictions": "",
        },
        PD,
        False,
    ),
    (
        "cc0",
        {
            "License": "cc0",
            "LicenseShortName": "CC0",
            "LicenseUrl": "http://creativecommons.org/publicdomain/zero/1.0/deed.en",
            "AttributionRequired": "false",
            "Copyrighted": "True",
        },
        CC0,
        False,
    ),
    (
        "pdm-url-only",
        {"LicenseUrl": "https://creativecommons.org/publicdomain/mark/1.0/"},
        PD,
        False,
    ),
    (
        "usgov-key",
        {"License": "pd-usgov-nasa", "LicenseShortName": "Public domain"},
        PD,
        False,
    ),
    (
        "pd-with-restrictions",
        {
            "License": "pd",
            "LicenseShortName": "Public domain",
            "Restrictions": "trademarked",
        },
        PD,
        True,
    ),
    (
        "cc-by",
        {
            "License": "cc-by-4.0",
            "LicenseShortName": "CC BY 4.0",
            "LicenseUrl": "https://creativecommons.org/licenses/by/4.0",
        },
        BY,
        False,
    ),
    (
        "cc-by-ported",
        {"License": "cc-by-3.0-us", "LicenseShortName": "CC BY 3.0 US"},
        BY,
        False,
    ),
    (
        "cc-by-sa",
        {
            "License": "cc-by-sa-4.0",
            "LicenseShortName": "CC BY-SA 4.0",
            "LicenseUrl": "https://creativecommons.org/licenses/by-sa/4.0",
        },
        SA,
        False,
    ),
    (
        "cc-by-sa-migrated",
        {"License": "cc-by-sa-3.0-migrated", "LicenseShortName": "CC BY-SA 3.0"},
        SA,
        False,
    ),
    (
        "cc-by-nc",
        {"License": "cc-by-nc-4.0", "LicenseShortName": "CC BY-NC 4.0"},
        RESTRICTED,
        False,
    ),
    (
        "cc-by-nd",
        {"License": "cc-by-nd-4.0", "LicenseShortName": "CC BY-ND 4.0"},
        RESTRICTED,
        False,
    ),
    (
        "cc-by-nc-sa",
        {"License": "cc-by-nc-sa-3.0", "LicenseShortName": "CC BY-NC-SA 3.0"},
        RESTRICTED,
        False,
    ),
    (
        "pd-but-copyrighted",
        {"License": "pd", "LicenseShortName": "Public domain", "Copyrighted": "True"},
        UNKNOWN,
        True,
    ),
    (
        "pd-but-attribution",
        {
            "License": "pd",
            "LicenseShortName": "Public domain",
            "AttributionRequired": "true",
        },
        UNKNOWN,
        True,
    ),
    (
        "fields-disagree",
        {"License": "cc-by-4.0", "LicenseShortName": "CC BY-SA 4.0"},
        UNKNOWN,
        True,
    ),
    ("pd-vs-by", {"License": "pd", "LicenseShortName": "CC BY 4.0"}, UNKNOWN, True),
    (
        "unrecognised-short-name",
        {"License": "pd", "LicenseShortName": "Copyrighted free use"},
        UNKNOWN,
        True,
    ),
    ("unrecognised-pd-variant", {"License": "pd-old-70"}, UNKNOWN, True),
    ("gfdl", {"LicenseShortName": "GFDL"}, UNKNOWN, True),
    ("dual-licence", {"LicenseShortName": "CC BY-SA 3.0, GFDL"}, UNKNOWN, True),
    (
        "non-cc-url",
        {"LicenseShortName": "CC BY 4.0", "LicenseUrl": "https://example.com/licence"},
        UNKNOWN,
        True,
    ),
    ("no-fields", {}, UNKNOWN, True),
    ("copyrighted-false-alone", {"Copyrighted": "False"}, UNKNOWN, True),
    ("age-alone", {"Copyrighted": "False", "DateTimeOriginal": "1890"}, UNKNOWN, True),
]

# (id, licenseurl, rights text, access_restricted, expected status, expected needs_review)
INTERNET_ARCHIVE_CASES: list[tuple[str, str | None, str | None, bool, RightsStatus, bool]] = [
    (
        "pdm-with-pd-text",
        PDM_URL,
        "Public Domain. No restrictions on use.",
        False,
        PD,
        False,
    ),
    (
        "pdm-alone",
        "https://creativecommons.org/publicdomain/mark/1.0/",
        None,
        False,
        PD,
        False,
    ),
    ("cc0-alone", CC0_URL, None, False, CC0, False),
    (
        "pd-text-without-licenseurl",
        None,
        "Public Domain. No restrictions on use.",
        False,
        UNKNOWN,
        True,
    ),
    ("nothing", None, None, False, UNKNOWN, True),
    (
        "pdm-contradicted-by-doubt",
        PDM_URL,
        COPYRIGHT_UNKNOWN_TEXT,
        False,
        UNKNOWN,
        True,
    ),
    (
        "pdm-contradicted-by-restriction",
        PDM_URL,
        "All rights reserved.",
        False,
        RESTRICTED,
        False,
    ),
    ("pdm-access-restricted", PDM_URL, None, True, UNKNOWN, True),
    (
        "fees-text",
        None,
        "Commercial use of images is subject to service fees.",
        False,
        RESTRICTED,
        False,
    ),
    (
        "nc-in-text",
        None,
        "Images may be used following Creative Commons CC BY-NC-SA 3.0 license.",
        False,
        RESTRICTED,
        False,
    ),
    ("cc-by", "https://creativecommons.org/licenses/by/4.0/", None, False, BY, False),
    (
        "cc-by-sa",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        None,
        False,
        SA,
        False,
    ),
    (
        "cc-by-nc",
        "https://creativecommons.org/licenses/by-nc/4.0/",
        None,
        False,
        RESTRICTED,
        False,
    ),
    (
        "cc-by-nc-nd-doubt",
        "https://creativecommons.org/licenses/by-nc-nd/4.0/",
        COPYRIGHT_UNKNOWN_TEXT,
        False,
        RESTRICTED,
        False,
    ),
    ("non-cc-url", "https://example.com/public-domain", None, False, UNKNOWN, True),
    ("list-like-url", PDM_URL + " " + CC0_URL, None, False, UNKNOWN, True),
]


def check_rights_cases() -> None:
    """Assert every table row against the current rights functions."""
    for case_id, texts, access_restricted, status, review in LOC_CASES:
        got = rights.assess_loc(texts, access_restricted=access_restricted)
        assert (got.status, got.needs_review) == (
            status,
            review,
        ), f"loc {case_id}: {got}"
    for case_id, meta, status, review in WIKIMEDIA_CASES:
        got = rights.assess_wikimedia(meta)
        assert (got.status, got.needs_review) == (
            status,
            review,
        ), f"wikimedia {case_id}: {got}"
    for case_id, url, text, access_restricted, status, review in INTERNET_ARCHIVE_CASES:
        got = rights.assess_internet_archive(licenseurl=url, rights=text, access_restricted=access_restricted)
        assert (got.status, got.needs_review) == (
            status,
            review,
        ), f"ia {case_id}: {got}"


# Outcome of running every fixture through its client. Keys are (provider, id).
EXPECTED_FIXTURE_STATUS: dict[tuple[str, str], RightsStatus] = {
    ("loc", "2025662344"): UNKNOWN,
    ("loc", "2004679630"): UNKNOWN,
    ("loc", "18015276"): UNKNOWN,
    ("loc", "2010630382"): UNKNOWN,
    ("wikimedia", "163834389"): PD,
    ("wikimedia", "3413544"): BY,
    ("wikimedia", "75555727"): SA,
    ("wikimedia", "183372324"): BY,
    ("wikimedia", "102629154"): CC0,
    ("wikimedia", "102412"): PD,
    ("internet_archive", "cmlpl_000770"): PD,
    ("internet_archive", "cscrm_001416"): UNKNOWN,
    ("internet_archive", "Smittys010"): RESTRICTED,
}


def load_fixture(name: str) -> Any:
    """Parse a JSON fixture under ``tests/fixtures/archival``."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_handler(request: httpx.Request) -> httpx.Response:
    """Serve the saved live responses for all three providers."""
    host, path = request.url.host, request.url.path
    if host == "www.loc.gov" and path == "/photos/":
        return httpx.Response(200, json=load_fixture("loc_search.json"))
    if host == "commons.wikimedia.org" and path == "/w/api.php":
        return httpx.Response(200, json=load_fixture("wikimedia_search.json"))
    if host == "archive.org" and path == "/advancedsearch.php":
        return httpx.Response(200, json=load_fixture("ia_advancedsearch.json"))
    if host == "archive.org" and path.startswith("/metadata/"):
        metadata = FIXTURES / f"ia_metadata_{path.rsplit('/', 1)[1]}.json"
        if metadata.exists():
            return httpx.Response(200, json=load_fixture(metadata.name))
    return httpx.Response(404)


async def collect_fixture_candidates() -> list[ArchivalCandidate]:
    """Run every provider client over the fixtures and return all candidates."""
    transport = httpx.MockTransport(fixture_handler)
    clients = (
        LibraryOfCongressClient(transport=transport),
        WikimediaCommonsClient(transport=transport),
        InternetArchiveClient(transport=transport),
    )
    candidates: list[ArchivalCandidate] = []
    try:
        for client in clients:
            candidates.extend(await client.search("standard oil", limit=10))
    finally:
        for client in clients:
            await client.close()
    return candidates


def check_fixture_statuses(candidates: list[ArchivalCandidate]) -> None:
    """Assert the fixture candidates carry exactly the expected rights status."""
    got = {(c.provider, c.provider_id): c.rights_status for c in candidates}
    assert got == EXPECTED_FIXTURE_STATUS
