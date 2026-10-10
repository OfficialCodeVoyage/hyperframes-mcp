"""Rights mapping: ambiguous evidence must never become public domain."""

import pytest

from hyperframes_mcp.archival import RightsStatus
from hyperframes_mcp.archival.rights import (
    TextSignal,
    assess_internet_archive,
    assess_loc,
    assess_wikimedia,
    classify_rights_text,
    combine_text_signals,
    parse_cc_token,
    parse_cc_url,
    worse,
)
from tests.archival.oracle import (
    INTERNET_ARCHIVE_CASES,
    LOC_CASES,
    WIKIMEDIA_CASES,
)

PUBLIC = (RightsStatus.PUBLIC_DOMAIN, RightsStatus.CC0)


@pytest.mark.parametrize("case", LOC_CASES, ids=[c[0] for c in LOC_CASES])
def test_loc_cases(case):
    _, texts, access_restricted, status, review = case
    got = assess_loc(texts, access_restricted=access_restricted)
    assert (got.status, got.needs_review) == (status, review)


@pytest.mark.parametrize("case", WIKIMEDIA_CASES, ids=[c[0] for c in WIKIMEDIA_CASES])
def test_wikimedia_cases(case):
    _, meta, status, review = case
    got = assess_wikimedia(meta)
    assert (got.status, got.needs_review) == (status, review)


@pytest.mark.parametrize("case", INTERNET_ARCHIVE_CASES, ids=[c[0] for c in INTERNET_ARCHIVE_CASES])
def test_internet_archive_cases(case):
    _, url, text, access_restricted, status, review = case
    got = assess_internet_archive(licenseurl=url, rights=text, access_restricted=access_restricted)
    assert (got.status, got.needs_review) == (status, review)


def test_every_ambiguous_case_is_unknown_and_flagged_never_public():
    """Any case the tables expect to be unknown must stay out of public/CC0."""
    ambiguous = (
        [(assess_loc(t, access_restricted=a), cid) for cid, t, a, s, _ in LOC_CASES if s is RightsStatus.UNKNOWN]
        + [(assess_wikimedia(m), cid) for cid, m, s, _ in WIKIMEDIA_CASES if s is RightsStatus.UNKNOWN]
        + [
            (assess_internet_archive(licenseurl=u, rights=t, access_restricted=a), cid)
            for cid, u, t, a, s, _ in INTERNET_ARCHIVE_CASES
            if s is RightsStatus.UNKNOWN
        ]
    )
    assert len(ambiguous) > 20
    for assessment, case_id in ambiguous:
        assert assessment.status not in PUBLIC, case_id
        assert assessment.needs_review, case_id
        assert assessment.reasons, case_id


@pytest.mark.parametrize(
    "text",
    [
        "No known restrictions on publication.",
        "no known copyright restrictions",
        "Rights status not evaluated.",
        "The rights status has not been determined.",
        "Copyright status unknown.",
        "This work may be protected by copyright.",
        "Possibly public domain",
        "Believed to be in the public domain",
        "Not in the public domain",
        "This is not a US Government work",
    ],
)
def test_doubt_wording_is_doubt(text):
    assert classify_rights_text(text) is TextSignal.DOUBT


@pytest.mark.parametrize(
    "text",
    [
        "Public domain",
        "Public Domain. No restrictions on use.",
        "Public domain (U.S. Government work)",
        "This image is a US Government work.",
    ],
)
def test_explicit_wording_is_public_domain(text):
    assert classify_rights_text(text) is TextSignal.EXPLICIT_PUBLIC_DOMAIN


@pytest.mark.parametrize("text", ["", "   ", None, "Photograph taken in 1895.", "Free to use."])
def test_unrelated_or_age_only_wording_is_neutral(text):
    assert classify_rights_text(text) is TextSignal.NEUTRAL


def test_combining_signals_takes_the_most_cautious():
    pd, doubt, restricted, neutral = (
        TextSignal.EXPLICIT_PUBLIC_DOMAIN,
        TextSignal.DOUBT,
        TextSignal.RESTRICTED,
        TextSignal.NEUTRAL,
    )
    assert combine_text_signals([pd, doubt]) is doubt
    assert combine_text_signals([pd, restricted]) is restricted
    assert combine_text_signals([pd, neutral]) is neutral
    assert combine_text_signals([pd, pd]) is pd
    assert combine_text_signals([]) is neutral


@pytest.mark.parametrize(
    ("url", "status", "licence_id"),
    [
        (
            "https://creativecommons.org/licenses/by/4.0/",
            RightsStatus.ATTRIBUTION_REQUIRED,
            "CC-BY-4.0",
        ),
        (
            "http://creativecommons.org/licenses/by/2.0/de/deed.de",
            RightsStatus.ATTRIBUTION_REQUIRED,
            "CC-BY-2.0",
        ),
        (
            "https://creativecommons.org/licenses/by-sa/3.0",
            RightsStatus.SHARE_ALIKE,
            "CC-BY-SA-3.0",
        ),
        (
            "https://creativecommons.org/licenses/by-nc/4.0/",
            RightsStatus.RESTRICTED,
            "CC-BY-NC-4.0",
        ),
        (
            "https://creativecommons.org/licenses/by-nd/4.0/",
            RightsStatus.RESTRICTED,
            "CC-BY-ND-4.0",
        ),
        (
            "https://creativecommons.org/licenses/by-nc-sa/4.0/",
            RightsStatus.RESTRICTED,
            "CC-BY-NC-SA-4.0",
        ),
        (
            "https://creativecommons.org/licenses/by-nc-nd/4.0/",
            RightsStatus.RESTRICTED,
            "CC-BY-NC-ND-4.0",
        ),
        (
            "http://creativecommons.org/publicdomain/zero/1.0/deed.en",
            RightsStatus.CC0,
            "CC0-1.0",
        ),
        (
            "https://creativecommons.org/publicdomain/mark/1.0/",
            RightsStatus.PUBLIC_DOMAIN,
            "PDM-1.0",
        ),
    ],
)
def test_parse_cc_url(url, status, licence_id):
    licence = parse_cc_url(url)
    assert licence is not None
    assert (licence.status, licence.licence_id) == (status, licence_id)


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://example.com/licenses/by/4.0/",
        "https://creativecommons.org.evil.example/licenses/by/4.0/",
        "https://creativecommons.org/licenses/by/4.0/ extra",
        "https://creativecommons.org/publicdomain/zero/2.0/",
        "https://creativecommons.org/licenses/zero/4.0/",
    ],
)
def test_parse_cc_url_rejects_anything_that_is_not_an_exact_cc_url(url):
    assert parse_cc_url(url) is None


@pytest.mark.parametrize(
    ("token", "status"),
    [
        ("cc-by-4.0", RightsStatus.ATTRIBUTION_REQUIRED),
        ("CC BY-SA 4.0", RightsStatus.SHARE_ALIKE),
        ("CC BY-NC 2.0", RightsStatus.RESTRICTED),
        ("cc0", RightsStatus.CC0),
        ("CC0", RightsStatus.CC0),
        ("PDM", RightsStatus.PUBLIC_DOMAIN),
    ],
)
def test_parse_cc_token(token, status):
    licence = parse_cc_token(token)
    assert licence is not None
    assert licence.status is status


@pytest.mark.parametrize(
    "token",
    [None, "", "GFDL", "CC BY-SA 3.0, GFDL", "cc-by", "cc-by-sa", "public domain"],
)
def test_parse_cc_token_rejects_compound_or_partial_names(token):
    assert parse_cc_token(token) is None


def test_worse_returns_the_more_restrictive_status():
    assert worse(RightsStatus.PUBLIC_DOMAIN, RightsStatus.UNKNOWN) is RightsStatus.UNKNOWN
    assert worse(RightsStatus.RESTRICTED, RightsStatus.UNKNOWN) is RightsStatus.RESTRICTED
    assert worse(RightsStatus.CC0, RightsStatus.ATTRIBUTION_REQUIRED) is (RightsStatus.ATTRIBUTION_REQUIRED)
