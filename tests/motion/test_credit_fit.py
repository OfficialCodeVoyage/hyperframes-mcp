"""The shorter, still true forms of a credit line, longest first: what to try when the time a picture leaves its credit
is too short for the whole text."""

from __future__ import annotations

from hyperframes_mcp.motion.credit_fit import shorter_credits

FULL = "U.S. National Archives and Records Administration, Wikimedia Commons. Public domain."


def test_the_host_goes_first_then_the_agencys_long_name():
    assert shorter_credits(FULL) == [
        "U.S. National Archives and Records Administration. Public domain.",
        "U.S. National Archives. Public domain.",
    ]


def test_the_licence_is_never_dropped_or_cut():
    for text in shorter_credits("Library of Congress, Prints and Photographs Division. Public domain."):
        assert text.endswith("Public domain.")
    assert shorter_credits("Library of Congress, Prints and Photographs Division. Public domain.") == [
        "Library of Congress. Public domain."
    ]


def test_a_cc_by_credit_keeps_its_title_and_its_creator_and_loses_only_the_host():
    assert shorter_credits('"Gold bars" by Ann Smith, Flickr. CC BY 4.0.') == ['"Gold bars" by Ann Smith. CC BY 4.0.']


def test_a_credit_with_nothing_to_drop_gives_nothing():
    assert shorter_credits("Bundesarchiv. CC BY-SA 3.0.") == []
    assert shorter_credits("Public domain.") == []
    assert shorter_credits("") == []


def test_every_shorter_form_is_shorter_and_none_repeats():
    forms = shorter_credits(FULL)

    assert len(set(forms)) == len(forms)
    assert all(len(form.split()) < len(FULL.split()) for form in forms)


def test_a_creator_written_surname_first_keeps_its_given_name():
    assert shorter_credits("Lange, Dorothea, Library of Congress. Public domain.") == [
        "Lange, Dorothea. Public domain."
    ]
    assert shorter_credits("Smith, John, Flickr. CC0.") == ["Smith, John. CC0."]
