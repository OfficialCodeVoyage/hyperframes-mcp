"""ArchivalCandidate invariants: unknown rights always need review."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hyperframes_mcp.archival import (
    ArchivalCandidate,
    Dimensions,
    Provenance,
    RightsStatus,
)
from tests.archival.helpers import make_candidate


def test_unknown_without_review_is_rejected():
    with pytest.raises(ValidationError, match="needs_review"):
        make_candidate(RightsStatus.UNKNOWN, needs_review=False)


@pytest.mark.parametrize("status", [RightsStatus.PUBLIC_DOMAIN, RightsStatus.CC0])
def test_public_or_cc0_without_recorded_source_fields_is_rejected(status):
    with pytest.raises(ValidationError, match="raw_rights_fields"):
        make_candidate(status, raw_rights_fields={})


def test_a_candidate_with_no_rights_information_defaults_to_unknown_and_review():
    bare = ArchivalCandidate(
        provider="loc",
        provider_id="1",
        source_url="https://www.loc.gov/item/1/",
        title="t",
        provenance=Provenance(
            retrieved_at=datetime(2026, 9, 30, tzinfo=UTC),
            api_url="https://www.loc.gov/",
        ),
    )
    assert bare.rights_status is RightsStatus.UNKNOWN
    assert bare.needs_review is True
    assert bare.usable_by_default is False


@pytest.mark.parametrize(
    ("status", "needs_review", "expected"),
    [
        (RightsStatus.PUBLIC_DOMAIN, False, True),
        (RightsStatus.CC0, False, True),
        (RightsStatus.ATTRIBUTION_REQUIRED, False, True),
        (RightsStatus.PUBLIC_DOMAIN, True, False),
        (RightsStatus.CC0, True, False),
        (RightsStatus.ATTRIBUTION_REQUIRED, True, False),
        (RightsStatus.SHARE_ALIKE, False, False),
        (RightsStatus.UNKNOWN, True, False),
        (RightsStatus.RESTRICTED, False, False),
    ],
)
def test_usable_by_default(status, needs_review, expected):
    assert make_candidate(status, needs_review=needs_review).usable_by_default is expected


def test_candidate_is_immutable():
    candidate = make_candidate()
    with pytest.raises(ValidationError):
        candidate.rights_status = RightsStatus.UNKNOWN


def test_dimensions_pixels_and_validation():
    assert Dimensions(width=4, height=3).pixels == 12
    with pytest.raises(ValidationError):
        Dimensions(width=0, height=3)


def test_candidate_round_trips_through_json():
    candidate = make_candidate(RightsStatus.ATTRIBUTION_REQUIRED, size=(640, 480))
    restored = type(candidate).model_validate_json(candidate.model_dump_json())
    assert restored == candidate
