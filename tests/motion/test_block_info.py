"""What the agent is told about the graphic blocks so it does not read their code: what each is for, where it sits on the
frame, how it stacks with the others and which variables it takes, from the registry and a table of places."""

from __future__ import annotations

import json

import pytest

from hyperframes_mcp.motion import block_info, registry


def blocks() -> dict[str, registry.Item]:
    return {item.name: item for item in registry.list_items() if item.type == "hyperframes:block"}


def test_every_block_has_a_place_and_nothing_else_has_one():
    # a new block fails here until someone says where it sits on the frame
    assert sorted(block_info.PLACES) == sorted(blocks())


def test_the_list_of_all_blocks_is_small_and_names_use_place_role_and_variables():
    listed = block_info.block_info()

    assert [entry["name"] for entry in listed["blocks"]] == sorted(blocks())
    assert len(json.dumps(listed)) < 8000
    for entry in listed["blocks"]:
        assert set(entry) == {"name", "use", "place", "role", "variables"}
        assert entry["use"] and entry["place"] and entry["role"]
        assert entry["variables"][0] == "durationSeconds"
    assert "block_info" in listed["next"]


def test_a_block_in_full_gives_its_variables_with_their_types_and_limits():
    stamp = block_info.block_info("date-stamp")

    assert stamp["name"] == "date-stamp"
    by_id = {v["id"]: v for v in stamp["variables"]}
    assert by_id["text"] == {
        "id": "text",
        "type": "string",
        "label": "The date",
        "maxLength": 30,
    }
    assert by_id["durationSeconds"]["min"] == 3 and by_id["durationSeconds"]["unit"] == "s"
    assert by_id["position"]["options"] == [
        "bottom-left",
        "top-left",
        "top-right",
        "bottom-right",
    ]
    # a default is a sample that never goes on screen, so it is not offered
    assert not any("default" in v for v in stamp["variables"])


def test_the_stacking_role_comes_from_the_registry_flags_and_the_row_kind():
    roles = {entry["name"]: entry["role"] for entry in block_info.block_info()["blocks"]}

    assert "paints the whole frame" in roles["photo-shot"]
    assert "credit" in roles["photo-shot"]
    assert "paints the whole frame" in roles["act-title"]
    assert "credit" not in roles["act-title"]
    assert "higher track" in roles["date-stamp"]  # a label goes over what covers the frame
    assert "transparent" in roles["counter"]
    assert "topmost" in roles["finish"]


def test_the_corner_a_label_takes_is_told_with_the_collisions_it_can_have():
    stamp = block_info.block_info("date-stamp")

    assert "corner" in stamp["place"]
    assert "credit-line" in stamp["note"]  # the default corner is the credit line's


def test_an_unknown_block_or_a_component_lists_the_blocks():
    with pytest.raises(registry.RegistryError) as caught:
        block_info.block_info("date-plate")
    with pytest.raises(registry.RegistryError):
        block_info.block_info("map-kit")  # installed with map-route, not a layer

    assert "date-stamp" in str(caught.value) and "photo-shot" in str(caught.value)
