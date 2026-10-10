"""rank_candidates: rights first, then resolution."""

import pytest

from hyperframes_mcp.archival import rank_candidates, rights
from tests.archival.helpers import BY, CC0, PD, RESTRICTED, SA, UNKNOWN, make_candidate


def _ids(candidates):
    return [c.provider_id for c in candidates]


def check_ranking_rules() -> None:
    """Assertions that fail when the rights tiers are reordered."""
    shuffled = [
        make_candidate(RESTRICTED, provider_id="restricted", size=(9000, 9000)),
        make_candidate(UNKNOWN, provider_id="unknown", size=(9000, 9000)),
        make_candidate(SA, provider_id="sa", size=(9000, 9000)),
        make_candidate(BY, provider_id="by", size=(9000, 9000)),
        make_candidate(PD, provider_id="pd", size=(100, 100)),
    ]
    assert _ids(rank_candidates(shuffled)) == [
        "pd",
        "by",
        "sa",
        "unknown",
        "restricted",
    ]


def test_rights_tiers_beat_resolution():
    check_ranking_rules()


def test_public_domain_and_cc0_share_a_tier_and_resolution_decides():
    ranked = rank_candidates(
        [
            make_candidate(PD, provider_id="pd-small", size=(640, 480)),
            make_candidate(CC0, provider_id="cc0-big", size=(3000, 2000)),
            make_candidate(BY, provider_id="by-huge", size=(8000, 6000)),
        ]
    )
    assert _ids(ranked) == ["cc0-big", "pd-small", "by-huge"]


def test_within_a_tier_larger_images_come_first_and_unknown_size_last():
    ranked = rank_candidates(
        [
            make_candidate(BY, provider_id="no-size"),
            make_candidate(BY, provider_id="small", size=(800, 600)),
            make_candidate(BY, provider_id="large", size=(4000, 3000)),
        ]
    )
    assert _ids(ranked) == ["large", "small", "no-size"]


def test_a_review_flag_sorts_after_a_clean_item_of_the_same_tier():
    ranked = rank_candidates(
        [
            make_candidate(PD, provider_id="flagged-big", needs_review=True, size=(5000, 4000)),
            make_candidate(PD, provider_id="clean-small", size=(500, 400)),
        ]
    )
    assert _ids(ranked) == ["clean-small", "flagged-big"]


def test_max_count_truncates_after_ranking():
    ranked = rank_candidates(
        [
            make_candidate(UNKNOWN, provider_id="u"),
            make_candidate(PD, provider_id="p"),
            make_candidate(BY, provider_id="b"),
        ],
        max_count=2,
    )
    assert _ids(ranked) == ["p", "b"]


def test_ties_keep_input_order_and_input_is_not_mutated():
    items = [make_candidate(BY, provider_id=str(i), size=(100, 100)) for i in range(4)]
    original = list(items)
    assert _ids(rank_candidates(items)) == ["0", "1", "2", "3"]
    assert items == original


def test_empty_input():
    assert rank_candidates([]) == []


@pytest.mark.parametrize("status", [UNKNOWN, RESTRICTED, SA])
def test_mutated_tier_order_fails_the_ranking_rules(monkeypatch, status):
    """Moving a worse tier to the top must be caught by the ranking assertions."""
    monkeypatch.setitem(rights.RIGHTS_ORDER, status, 0)
    with pytest.raises(AssertionError):
        check_ranking_rules()
