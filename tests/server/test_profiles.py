"""Several profiles: a project gets the house rules, picture settings, search preferences, picture style and spend limits of
its own profile, and a project of another profile gets none of them. The second profile, ``maps-and-money``, is a bundle
in a temporary config folder (``tests.motion.profile_helpers.profiles_root``) with its own ``edit.yaml``, image style and
spend limits; the repository's config holds no second profile."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyperframes_mcp.motion import generate, manifest, spend
from tests.motion.spend_helpers import earlier_spend
from tests.motion.test_project_edit import composed, two_shots
from tests.server.test_tools import call, text

MAPS = "maps-and-money"
MAPS_EDIT = """\
picture_quality: medium
picture_format: png
house_rules:
  - Flat-colour maps on cream paper; no engraving, no people.
  - Credit lines stay on screen for the whole picture.
search:
  sources: [wikimedia, loc]
  allow_by: true
"""
MAPS_LIMITS = """\
spend:
  per_call_usd: 1
  per_project_usd: 10
  profiles:
    maps-and-money:
      per_call_usd: 0.5
      per_project_usd: 6
"""


@pytest.fixture
def projects(tmp_path, monkeypatch, profiles_root):
    """One project of the default profile, one of ``maps-and-money`` and one of ``history`` (a profile with no bundle)."""
    profiles_root.write(MAPS, "profile.yaml", "niche: economic geography\n")
    profiles_root.write(MAPS, "edit.yaml", MAPS_EDIT)
    profiles_root.write(MAPS, "image_style.yaml", 'prefix: "FLAT-MAP-LOOK. Scene: "\n')
    limits = tmp_path / "limits.yaml"
    limits.write_text(MAPS_LIMITS, encoding="utf-8")
    monkeypatch.setattr(spend, "CONFIG_PATH", limits)
    made = {}
    for slug in ("default", MAPS, "history"):
        root = composed(tmp_path / slug, two_shots())[0]
        manifest.write_manifest(root, manifest.ProjectManifest(profile=slug))
        made[slug] = str(root)
    return made


@pytest.mark.asyncio
async def test_each_project_is_told_the_house_rules_of_its_own_profile_and_none_of_anothers(
    projects,
):
    default = (await call("project_info", {"project": projects["default"]})).structured_content
    maps = (await call("project_info", {"project": projects[MAPS]})).structured_content
    bare = (await call("project_info", {"project": projects["history"]})).structured_content

    assert (default["profile"], maps["profile"]) == ("default", MAPS)
    assert default["house_rules"] == []
    assert maps["house_rules"] == [
        "Flat-colour maps on cream paper; no engraving, no people.",
        "Credit lines stay on screen for the whole picture.",
    ]
    assert "engraved" not in " ".join(maps["house_rules"])
    assert default["edit_yaml"] is None
    assert maps["edit_yaml"].endswith(f"{MAPS}/edit.yaml")
    # a profile without an edit.yaml has no rules, and none of the default profile's
    assert bare["edit_yaml"] is None and bare["house_rules"] == []


@pytest.mark.asyncio
async def test_style_info_gives_the_profiles_settings_and_its_own_picture_style(
    projects,
):
    default = (await call("style_info", {"project": projects["default"]})).structured_content
    maps = (await call("style_info", {"project": projects[MAPS]})).structured_content

    assert maps["profile"] == MAPS
    assert (
        maps["house_rules"]
        == (await call("project_info", {"project": projects[MAPS]})).structured_content["house_rules"]
    )
    assert (maps["picture_quality"], maps["picture_format"]) == ("medium", "png")
    assert maps["search"] == {"sources": ["wikimedia", "loc"], "allow_by": True}
    assert maps["prompt_frame"].startswith("FLAT-MAP-LOOK. Scene: <subject>")
    assert maps["edit_yaml"].endswith(f"{MAPS}/edit.yaml")
    assert (default["picture_quality"], default["picture_format"]) == ("high", "jpg")
    assert default["search"]["allow_by"] is False and len(default["search"]["sources"]) == 4
    assert "FLAT-MAP-LOOK" not in default["prompt_frame"]
    # reference stills are a later option: named, empty, and said not to be used
    assert maps["style_references"] == {"stills": [], "used": False}


@pytest.mark.asyncio
async def test_each_project_counts_its_spend_against_its_own_profiles_limits(projects):
    default = (await call("project_info", {"project": projects["default"]})).structured_content["spend"]
    maps = (await call("project_info", {"project": projects[MAPS]})).structured_content["spend"]

    assert default["limits"]["per_project_usd"] == 10.0 and default["limits"]["per_call_usd"] == 1.0
    assert maps["limits"]["per_project_usd"] == 6.0 and maps["limits"]["per_call_usd"] == 0.5


@pytest.mark.asyncio
async def test_a_confirm_is_refused_at_the_profiles_own_per_video_limit(projects, paid):
    quoted = (
        await call(
            "quote_picture",
            {"request": "a harbour", "name": "harbour", "project": projects[MAPS]},
        )
    ).structured_content
    earlier_spend(Path(projects[MAPS]), 5.99, at=datetime.now(UTC))

    result = await call("confirm_spend", {"quote_id": quoted["quote_id"], "project": projects[MAPS]})

    assert result.is_error and "has spent $5.99 of $6.00" in text(result)
    assert paid == []


@pytest.mark.asyncio
async def test_a_quote_uses_the_profiles_picture_quality_and_format_when_the_call_names_none(
    projects,
):
    medium, high = generate.picture_price("medium"), generate.picture_price("high")
    assert medium < high

    maps = (
        await call(
            "quote_picture",
            {"request": "a harbour", "name": "harbour", "project": projects[MAPS]},
        )
    ).structured_content
    default = (
        await call(
            "quote_picture",
            {"request": "a harbour", "name": "harbour", "project": projects["default"]},
        )
    ).structured_content
    asked = (
        await call(
            "quote_picture",
            {
                "request": "a harbour",
                "name": "h2",
                "quality": "high",
                "project": projects[MAPS],
            },
        )
    ).structured_content

    assert (maps["price_usd"], maps["out"]) == (
        medium,
        "assets/media/edits/harbour.png",
    )
    assert (default["price_usd"], default["out"]) == (
        high,
        "assets/media/edits/harbour.jpg",
    )
    assert asked["price_usd"] == high  # a quality in the call wins
    assert maps["prompt"].startswith("FLAT-MAP-LOOK. Scene: a harbour")
    assert "FLAT-MAP-LOOK" not in default["prompt"]


@pytest.mark.asyncio
async def test_a_bad_edit_yaml_is_an_error_in_words_that_names_the_file_and_the_key(projects, profiles_root):
    path = profiles_root.write(MAPS, "edit.yaml", "picture_qualty: medium\n")

    for tool, arguments in (
        ("project_info", {}),
        ("style_info", {}),
        ("quote_picture", {"request": "a harbour", "name": "harbour"}),
        ("search_media", {"query": "oil refinery"}),
    ):
        result = await call(tool, arguments | {"project": projects[MAPS]})
        assert result.is_error, tool
        assert str(path) in text(result) and "picture_qualty" in text(result), tool
    # the other profiles' projects are not affected
    assert not (await call("project_info", {"project": projects["default"]})).is_error


@pytest.mark.asyncio
async def test_search_media_asks_the_profiles_sources_and_allows_cc_by_as_its_edit_yaml_says(projects, archives):
    maps = await call("search_media", {"query": "oil refinery", "project": projects[MAPS]})

    assert [line.split(":")[0] for line in maps.structured_content["tried"]] == [
        "wikimedia",
        "loc",
    ]
    assert {r.url.host for r in archives} == {"commons.wikimedia.org", "www.loc.gov"}
    assert maps.structured_content["allow_by"] is True
    assert any(h["licence"] == "CC-BY-4.0" for h in maps.structured_content["hits"])
    archives.clear()

    default = await call("search_media", {"query": "oil refinery", "project": projects["default"]})
    refused = await call(
        "search_media",
        {"query": "oil refinery", "allow_by": False, "project": projects[MAPS]},
    )

    assert len(default.structured_content["tried"]) == 4 and default.structured_content["allow_by"] is False
    assert all(h["licence"] == "Public domain" for h in default.structured_content["hits"])
    assert refused.structured_content["allow_by"] is False  # the call's own word wins over the profile's
    assert all(h["licence"] == "Public domain" for h in refused.structured_content["hits"])
