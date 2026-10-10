"""A mutated rights mapping must make the rights oracle fail.

Each mutant breaks the real mapping in a way that could let an ambiguous or
restricted item through. The oracle tables (``oracle.py``) must catch every
one, either against the table of rights statements or against the saved live
fixtures, so a change that loosens the mapping cannot pass the suite.
"""

import re

import pytest

from hyperframes_mcp.archival import RightsStatus, rights
from hyperframes_mcp.archival.rights import CcLicence, TextSignal
from tests.archival.oracle import (
    check_fixture_statuses,
    check_rights_cases,
    collect_fixture_candidates,
)

_NEVER = re.compile(r"(?!x)x")
_real_classify = rights.classify_rights_text
_real_assess_loc = rights.assess_loc


def _no_known_restrictions_is_public_domain(monkeypatch):
    def mutated(text):
        if text and "no known" in text.lower():
            return TextSignal.EXPLICIT_PUBLIC_DOMAIN
        return _real_classify(text)

    monkeypatch.setattr(rights, "classify_rights_text", mutated)


def _every_statement_is_public_domain(monkeypatch):
    monkeypatch.setattr(rights, "classify_rights_text", lambda text: TextSignal.EXPLICIT_PUBLIC_DOMAIN)


def _share_alike_needs_only_attribution(monkeypatch):
    monkeypatch.setitem(rights.CC_STATUS_BY_VARIANT, "by-sa", RightsStatus.ATTRIBUTION_REQUIRED)


def _non_commercial_needs_only_attribution(monkeypatch):
    monkeypatch.setitem(rights.CC_STATUS_BY_VARIANT, "by-nc", RightsStatus.ATTRIBUTION_REQUIRED)


def _no_derivatives_needs_only_attribution(monkeypatch):
    monkeypatch.setitem(rights.CC_STATUS_BY_VARIANT, "by-nd", RightsStatus.ATTRIBUTION_REQUIRED)


def _any_commons_pd_prefix(monkeypatch):
    monkeypatch.setattr(rights, "WIKIMEDIA_PUBLIC_DOMAIN_PREFIXES", ("",))


def _any_cc_url_is_public_domain(monkeypatch):
    monkeypatch.setattr(
        rights,
        "parse_cc_url",
        lambda url: CcLicence(RightsStatus.PUBLIC_DOMAIN, "X", url) if url else None,
    )


def _conflicts_resolve_to_the_lenient_status(monkeypatch):
    monkeypatch.setattr(
        rights,
        "worse",
        lambda a, b: a if rights.RIGHTS_ORDER[a] <= rights.RIGHTS_ORDER[b] else b,
    )


def _doubt_wording_ignored(monkeypatch):
    monkeypatch.setattr(rights, "_DOUBT", _NEVER)


def _negation_ignored(monkeypatch):
    monkeypatch.setattr(rights, "_NEGATION", _NEVER)


def _loc_ignores_access_restricted(monkeypatch):
    monkeypatch.setattr(
        rights,
        "assess_loc",
        lambda texts, *, access_restricted=False: _real_assess_loc(texts),
    )


# Mutants the table of rights statements must catch.
MUTANTS = {
    "no-known-restrictions-is-pd": _no_known_restrictions_is_public_domain,
    "every-statement-is-pd": _every_statement_is_public_domain,
    "share-alike-as-attribution": _share_alike_needs_only_attribution,
    "nc-as-attribution": _non_commercial_needs_only_attribution,
    "nd-as-attribution": _no_derivatives_needs_only_attribution,
    "any-commons-pd-prefix": _any_commons_pd_prefix,
    "any-cc-url-is-pd": _any_cc_url_is_public_domain,
    "lenient-conflicts": _conflicts_resolve_to_the_lenient_status,
    "doubt-wording-ignored": _doubt_wording_ignored,
    "negation-ignored": _negation_ignored,
    "loc-ignores-access-restricted": _loc_ignores_access_restricted,
}
# The subset that also shows on the saved live responses. The rest only show on
# statements the fixtures do not contain (for example a CC BY-NC file).
FIXTURE_MUTANTS = (
    "no-known-restrictions-is-pd",
    "every-statement-is-pd",
    "share-alike-as-attribution",
    "any-cc-url-is-pd",
    "lenient-conflicts",
)


def test_unmutated_mapping_passes_both_oracles():
    check_rights_cases()


@pytest.mark.asyncio
async def test_unmutated_fixtures_pass():
    check_fixture_statuses(await collect_fixture_candidates())


@pytest.mark.parametrize("name", MUTANTS)
def test_mutated_mapping_fails_the_rights_cases(monkeypatch, name):
    MUTANTS[name](monkeypatch)
    with pytest.raises(AssertionError):
        check_rights_cases()


@pytest.mark.parametrize("name", FIXTURE_MUTANTS)
@pytest.mark.asyncio
async def test_mutated_mapping_fails_the_fixture_oracle(monkeypatch, name):
    MUTANTS[name](monkeypatch)
    with pytest.raises(AssertionError):
        check_fixture_statuses(await collect_fixture_candidates())
