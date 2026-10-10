"""A profile's edit settings (``~/.hyperframes-mcp/config/profiles/<name>/edit.yaml``): the house rules, the picture defaults and the search
preferences the video MCP server reads for a project of that profile."""

from __future__ import annotations

from typing import get_args

import pytest

from hyperframes_mcp.motion import edit_config, generate
from hyperframes_mcp.motion.edit_config import EditConfig, EditConfigError, load_edit_config


def test_the_default_profile_has_no_file_and_every_setting_at_its_default():
    default = load_edit_config("default")

    assert default == EditConfig()
    assert default.house_rules == []
    assert not edit_config.edit_file("default").is_file()


def test_a_profile_without_the_file_gets_the_defaults_and_none_of_another_profiles_rules(
    profiles_root,
):
    profiles_root.write("default", "edit.yaml", "house_rules:\n  - Flat colour.\n")
    profiles_root.write("profile-two", "profile.yaml", "niche: finance\n")

    two = load_edit_config("profile-two")

    assert two == EditConfig()
    assert two.house_rules == [] and two.picture_quality == "high"
    assert not edit_config.edit_file("profile-two").is_file()
    assert load_edit_config("default").house_rules == ["Flat colour."]  # the default's are its own


def test_a_file_that_names_some_settings_keeps_the_defaults_of_the_rest(profiles_root):
    profiles_root.write(
        "maps",
        "edit.yaml",
        "picture_quality: medium\nhouse_rules:\n  - Flat colour, no engraving.\n",
    )

    maps = load_edit_config("maps")

    assert maps.picture_quality == "medium"
    assert maps.house_rules == ["Flat colour, no engraving."]
    assert (maps.picture_format, maps.style_references) == ("jpg", [])
    assert maps.search.allow_by is False
    assert maps.search.sources == list(edit_config.SOURCES)


def test_an_empty_file_is_the_defaults(profiles_root):
    profiles_root.write("maps", "edit.yaml", "# nothing yet\n")

    assert load_edit_config("maps") == EditConfig()


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("colour_grade: warm\n", "colour_grade"),  # a misspelt or unknown key
        ("picture_quality: ultra\n", "picture_quality"),
        ("picture_format: gif\n", "picture_format"),
        ("house_rules: be nice\n", "house_rules"),  # a list, not a sentence
        ("house_rules: ['']\n", "house_rules"),
        ("style_references: [/etc/passwd]\n", "style_references"),
        ("style_references: [../other/still.jpg]\n", "style_references"),
        ("search: {sources: [flickr]}\n", "search.sources"),
        ("search: {sources: [loc, loc]}\n", "search.sources"),
        ("search: {sources: []}\n", "search.sources"),
        ("search: {allow_by: 'yes please'}\n", "search.allow_by"),
        ("search: {order: [loc]}\n", "search.order"),
        ("- a list\n- not a mapping\n", "mapping"),
        ("picture_quality: [high\n", "YAML"),
    ],
)
def test_a_wrong_value_or_an_unknown_key_is_an_error_that_names_the_file_and_the_key_never_a_default(
    profiles_root, text, complaint
):
    path = profiles_root.write("maps", "edit.yaml", text)

    with pytest.raises(EditConfigError) as error:
        load_edit_config("maps")

    assert str(path) in str(error.value) and complaint in str(error.value)


def test_a_profile_slug_that_is_not_one_is_an_error_in_words():
    with pytest.raises(EditConfigError, match="is not a profile name"):
        load_edit_config("Not A Slug")


def test_the_format_choices_are_ones_the_picture_code_writes():
    assert {f".{kind}" for kind in edit_config.PICTURE_FORMAT_CHOICES} <= set(generate.PICTURE_FORMATS)
    assert set(edit_config.PICTURE_FORMAT_CHOICES) == set(
        get_args(EditConfig.model_fields["picture_format"].annotation)
    )
