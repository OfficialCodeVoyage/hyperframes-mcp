"""Attribution strings and the description block."""

from hyperframes_mcp.archival import (
    build_attribution_block,
    build_credit_line,
    credit_line,
    with_attribution,
)
from tests.archival.helpers import BY, CC0, PD, RESTRICTED, SA, UNKNOWN, make_candidate


def _line(status, **overrides):
    fields = {
        "title": "Oil refinery",
        "creator": "Jane Doe",
        "date": "2018",
        "provider": "wikimedia",
        "source_url": "https://commons.wikimedia.org/wiki/File:Oil.jpg",
        "licence_id": "CC-BY-4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0",
    }
    required_text = overrides.pop("required_text", None)
    fields.update(overrides)
    candidate = make_candidate(status, **fields)
    return build_credit_line(candidate, required_text=required_text)


def test_attribution_line_has_title_creator_source_and_licence():
    line = _line(BY)
    assert line == (
        '"Oil refinery" by Jane Doe (2018). '
        "Source: Wikimedia Commons, https://commons.wikimedia.org/wiki/File:Oil.jpg. "
        "Licensed under CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0)."
    )


def test_share_alike_line_names_the_licence():
    assert "Licensed under CC-BY-SA-4.0" in _line(SA, licence_id="CC-BY-SA-4.0")


def test_public_domain_cc0_unknown_and_restricted_lines_state_their_status():
    assert _line(PD, licence_id="PD", licence_url=None).endswith("Public domain.")
    assert _line(CC0).endswith("CC0 1.0 public domain dedication.")
    assert _line(UNKNOWN).endswith("Rights status not confirmed.")
    assert _line(RESTRICTED).endswith("Restricted: not cleared for use.")


def test_missing_creator_and_date_are_left_out_and_no_double_full_stop():
    line = _line(PD, creator=None, date=None)
    assert line.startswith('"Oil refinery". Source:')
    assert ".." not in _line(PD, creator="Schmidt Litho. Co.")


def test_rights_holder_supplied_text_replaces_the_default_lead():
    line = _line(BY, required_text="Photo: Jane Doe / Example Agency")
    assert line.startswith("Photo: Jane Doe / Example Agency. Source:")
    assert '"Oil refinery"' not in line


def test_credit_line_prefers_the_stored_text_and_otherwise_builds_one():
    stored = make_candidate(BY, attribution_text="Stored credit.")
    assert credit_line(stored) == "Stored credit."
    built = make_candidate(BY, attribution_text=None)
    assert credit_line(built).startswith('"Example" by A. Creator (1900). Source:')


def test_block_credits_required_items_first_then_courtesy_credits():
    block = build_attribution_block(
        [
            make_candidate(PD, provider_id="pd", attribution_text="PD credit."),
            make_candidate(BY, provider_id="by", attribution_text="BY credit."),
            make_candidate(CC0, provider_id="cc0", attribution_text="CC0 credit."),
        ]
    )
    assert block.credits == ("BY credit.", "PD credit.", "CC0 credit.")
    assert block.skipped == ()
    assert block.text == "Image credits:\n- BY credit.\n- PD credit.\n- CC0 credit."


def test_share_alike_is_skipped_by_default_and_marked_not_for_default_use():
    share_alike = make_candidate(SA, provider_id="sa", attribution_text="SA credit.")
    block = build_attribution_block([share_alike])
    assert block.credits == ()
    assert block.text == ""
    assert [(s.candidate.provider_id, s.reason) for s in block.skipped] == [
        ("sa", "share-alike licence: not for default use")
    ]


def test_share_alike_is_credited_when_explicitly_allowed():
    block = build_attribution_block(
        [
            make_candidate(PD, provider_id="pd", attribution_text="PD credit."),
            make_candidate(SA, provider_id="sa", attribution_text="SA credit."),
        ],
        allow_share_alike=True,
    )
    assert block.credits == ("SA credit.", "PD credit.")


def test_unknown_restricted_and_review_flagged_items_are_never_credited():
    candidates = [
        make_candidate(UNKNOWN, provider_id="unknown"),
        make_candidate(RESTRICTED, provider_id="restricted"),
        make_candidate(
            PD,
            provider_id="flagged",
            needs_review=True,
            review_reasons=("Commons restrictions: trademarked",),
        ),
        make_candidate(BY, provider_id="by-flagged", needs_review=True),
    ]
    block = build_attribution_block(candidates, allow_share_alike=True)
    assert block.credits == ()
    reasons = {s.candidate.provider_id: s.reason for s in block.skipped}
    assert reasons["unknown"] == "rights status unknown"
    assert reasons["restricted"] == "restricted licence"
    assert reasons["flagged"] == "needs review: Commons restrictions: trademarked"
    assert reasons["by-flagged"].startswith("needs review")


def test_duplicate_credits_are_listed_once():
    block = build_attribution_block(
        [
            make_candidate(BY, provider_id="1", attribution_text="Same credit."),
            make_candidate(BY, provider_id="2", attribution_text="Same credit."),
        ]
    )
    assert block.credits == ("Same credit.",)


def test_with_attribution_fills_the_credit_line_and_keeps_a_creditable_item_clean():
    candidate = make_candidate(BY, attribution_text=None)

    credited = with_attribution(candidate)

    assert credited.attribution_text == build_credit_line(candidate)
    assert credited.needs_review is False
    assert credited.review_reasons == ()


def test_with_attribution_flags_a_credit_required_item_with_nobody_to_credit():
    candidate = make_candidate(BY, creator=None, review_reasons=("earlier reason",))

    credited = with_attribution(candidate)

    assert credited.needs_review is True
    assert credited.usable_by_default is False
    assert credited.review_reasons == (
        "earlier reason",
        "no creator or attribution text to credit",
    )


def test_with_attribution_accepts_rights_holder_text_instead_of_a_creator():
    candidate = make_candidate(SA, creator=None)

    credited = with_attribution(candidate, required_text="Photo: Example Agency")

    assert credited.needs_review is False
    assert credited.attribution_text.startswith("Photo: Example Agency. Source:")


def test_with_attribution_does_not_flag_public_domain_without_a_creator():
    credited = with_attribution(make_candidate(PD, creator=None))

    assert credited.needs_review is False
