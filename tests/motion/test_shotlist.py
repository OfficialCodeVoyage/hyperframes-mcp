"""The shot list models and the registry rules: each rule has a sample that fails and one that passes."""

from __future__ import annotations

import dataclasses
import json

import pytest

from hyperframes_mcp.motion import registry
from hyperframes_mcp.motion.shotlist import (
    Cue,
    ShotListError,
    load_shotlist,
    parse_shotlist,
    validate_layers,
)
from tests.motion.composer_helpers import (
    edited,
    fake_registry,
    fake_shotlist,
    photo_layer,
    sample_shotlist,
)

LAYER_1 = ("shots", 0, "layers", 1)


def problems(raw, reg=None):
    return validate_layers(parse_shotlist(raw), reg)


def test_the_sample_shot_list_is_valid_against_the_real_registry():
    shotlist = parse_shotlist(sample_shotlist())

    assert [shot.id for shot in shotlist.shots] == ["s01", "s02", "s03"]
    assert shotlist.shots[0].transition_in.type == "cut"
    assert shotlist.shots[1].transition_in.seconds == 0.5
    assert validate_layers(shotlist) == []


def test_a_cue_is_a_time_with_defaults_and_a_number_is_a_time():
    raw = edited(
        sample_shotlist(),
        (("shots", 1, "start"), {"cue": "the gold was"}),
        (("shots", 2, "start"), {"cue": "gone", "occurrence": 2, "offset": -0.25}),
    )

    shotlist = parse_shotlist(raw)

    assert shotlist.shots[0].start == 0
    assert shotlist.shots[1].start == Cue(cue="the gold was")
    assert shotlist.shots[1].start.occurrence == 1 and shotlist.shots[1].start.offset == 0
    assert shotlist.shots[2].start.occurrence == 2
    assert shotlist.shots[2].start.offset == -0.25


def test_a_minimal_shot_list_gets_a_cut_and_the_default_master():
    raw = sample_shotlist()
    del raw["audio"]["master"], raw["audio"]["music"], raw["audio"]["sfx"]

    shotlist = parse_shotlist(raw)

    assert shotlist.audio.master.lufs == -14.0
    assert shotlist.audio.master.true_peak_db == -1.0
    assert shotlist.audio.music is None and shotlist.audio.sfx == []
    assert shotlist.shots[2].transition_in.type == "cut"


@pytest.mark.parametrize(
    "path, value, expected",
    [
        ((), None, "version"),
        (("surprise",), 1, "surprise: Extra inputs are not permitted"),
        (("shots", 0, "surprise"), 1, "shot 's01', surprise: Extra"),
        (
            ("shots", 0, "layers", 0, "surprise"),
            1,
            "shot 's01', layer 1 (act-title), surprise: Extra",
        ),
        (("audio", "voice", "volume"), 1, "audio, voice, volume: Extra"),
        (("meta", "language"), "fr", "meta, language"),
        (("version",), 2, "version"),
        (("shots", 1, "id"), "bad id!", "shot 'bad id!', id"),
        (("shots", 1, "start"), -1, "shot 's02', start"),
        (("shots", 1, "start"), True, "shot 's02', start"),
        (("shots", 1, "start"), "soon", "shot 's02', start"),
        (("shots", 1, "start"), {"cue": ""}, "shot 's02', start, cue"),
        (
            ("shots", 1, "start"),
            {"cue": "x", "occurrence": 0},
            "shot 's02', start, occurrence",
        ),
        (("shots", 1, "start"), {"cue": "x", "extra": 1}, "shot 's02', start, extra"),
        (("shots", 1, "duration"), 0.0, None),
        (
            ("shots", 1, "transition_in", "type"),
            "wipe",
            "shot 's02', transition_in, type",
        ),
        (("shots", 0, "layers"), [], "shot 's01', layers"),
        (("shots", 0, "layers", 0, "at"), -0.5, "shot 's01', layer 1 (act-title), at"),
        (
            ("shots", 0, "layers", 0, "duration"),
            0,
            "shot 's01', layer 1 (act-title), duration",
        ),
        (
            ("shots", 0, "layers", 0, "variables"),
            [],
            "shot 's01', layer 1 (act-title), variables",
        ),
        (
            ("audio", "music", "duck_under_voice_db"),
            3,
            "audio, music, duck_under_voice_db",
        ),
        (("audio", "sfx", 0, "at"), {"phrase": "x"}, "audio, sfx, 0, at"),
        (("audio", "master", "true_peak_db"), 1.0, "audio, master, true_peak_db"),
    ],
)
def test_a_shape_error_is_refused_and_names_where_it_is(path, value, expected):
    raw = sample_shotlist()
    if not path:
        del raw["version"]
    elif path == ("surprise",) or path[-1] == "surprise":
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    else:
        raw = edited(raw, (path, value))

    if expected is None:
        # a zero-second shot parses; the composer refuses it once the times are resolved
        parse_shotlist(raw)
        return
    with pytest.raises(ShotListError) as error:
        parse_shotlist(raw)
    assert expected in str(error.value)


def test_two_shots_with_the_same_id_are_refused():
    raw = edited(sample_shotlist(), (("shots", 2, "id"), "s01"))

    with pytest.raises(ShotListError, match="shot id 's01' is used twice"):
        parse_shotlist(raw)


def test_every_shape_problem_is_reported_together():
    raw = edited(
        sample_shotlist(),
        (("shots", 0, "layers", 0, "at"), -1),
        (("shots", 2, "id"), "no way"),
    )

    with pytest.raises(ShotListError) as error:
        parse_shotlist(raw)

    assert len(error.value.problems) == 2


def test_load_shotlist_reads_a_file_and_reports_a_broken_one(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(sample_shotlist()), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")

    assert load_shotlist(good).meta.title == "Test video"
    with pytest.raises(ShotListError, match="cannot read the shot list"):
        load_shotlist(broken)
    with pytest.raises(ShotListError, match="cannot read the shot list"):
        load_shotlist(tmp_path / "missing.json")


# -- the registry rules ---------------------------------------------------------------------------------------


def test_an_unknown_component_is_refused_and_the_registry_is_listed():
    raw = edited(sample_shotlist(), (LAYER_1 + ("component",), "sparkle"))

    [message] = problems(raw)

    assert message.startswith("shot 's01', layer 2 (sparkle): no component 'sparkle'")
    assert "lower-third" in message


def test_a_variable_the_block_does_not_declare_is_refused():
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"]["colour"] = "red"

    [message] = problems(raw)

    assert "shot 's01', layer 2 (lower-third), variable 'colour'" in message
    assert "declares durationSeconds, name, role, side" in message


def test_duration_seconds_is_never_set_by_a_shot():
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"]["durationSeconds"] = 6

    [message] = problems(raw)

    assert "variable 'durationSeconds'" in message
    assert "the composer sets it from the layer's slot" in message


def test_a_string_or_number_variable_that_is_not_given_is_refused_with_no_sample_fallback():
    raw = sample_shotlist()
    del raw["shots"][0]["layers"][1]["variables"]["role"]

    [message] = problems(raw)

    assert "shot 's01', layer 2 (lower-third), variable 'role' (string): not given" in message
    assert "never goes on screen" in message

    counter = {
        "component": "counter",
        "at": 0,
        "variables": {"prefix": "", "suffix": "bars", "label": "of gold"},
    }
    raw = edited(sample_shotlist(), (("shots", 2, "layers"), [counter]))
    messages = problems(raw)
    assert [m.split(", ")[2] for m in messages] == [
        "variable 'from' (number): not given. A block's default is a sample and never goes on screen: write the value (an empty string if it is not used)",
        "variable 'to' (number): not given. A block's default is a sample and never goes on screen: write the value (an empty string if it is not used)",
        "variable 'decimals' (number): not given. A block's default is a sample and never goes on screen: write the value (an empty string if it is not used)",
    ]


def test_an_empty_string_counts_as_given_and_enums_and_booleans_may_default():
    raw = sample_shotlist()
    variables = raw["shots"][0]["layers"][0]["variables"]
    assert variables["subtitle"] == ""
    lower_third = raw["shots"][0]["layers"][1]["variables"]
    del lower_third["side"]  # an enum: the block's default is a position, not a sample figure
    counter = {
        "component": "counter",
        "at": 0,
        "variables": {
            "from": 0,
            "to": 5,
            "prefix": "",
            "suffix": "",
            "decimals": 0,
            "label": "",
        },  # thousandsSeparator (boolean) left out
    }
    raw["shots"][2]["layers"] = [counter]

    assert problems(raw) == []


def test_a_layer_may_opt_into_sample_values_unless_the_video_is_to_be_published():
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1] = {
        "component": "lower-third",
        "at": 0.3,
        "sample": True,
        "variables": {},
    }

    assert problems(raw) == []

    raw["meta"]["publish"] = True
    [message] = problems(raw)
    assert "shot 's01', layer 2 (lower-third)" in message
    assert '"sample": true is for galleries and tests' in message
    assert '"publish": true refuses it' in message


@pytest.mark.parametrize(
    "variable, value, fragment",
    [
        ("name", 5, "must be text"),
        ("name", "x" * 41, "is 41 characters, the block takes at most 40"),
        ("side", "middle", "must be one of left, right"),
    ],
)
def test_a_value_of_the_wrong_kind_or_size_is_refused(variable, value, fragment):
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"][variable] = value

    [message] = problems(raw)

    assert f"variable {variable!r}" in message and fragment in message


def test_numbers_booleans_and_bounds_are_checked_against_the_declaration(tmp_path):
    reg = fake_registry(tmp_path)

    def check(**changes):
        raw = fake_shotlist()
        raw["shots"][0]["layers"][0] = photo_layer(variables=changes)
        return problems(raw, reg)

    assert check() == []
    assert "must be a number" in check(scrim="a lot")[0]
    assert "must be a number" in check(scrim=True)[0]
    assert "the block's maximum is 1" in check(scrim=1.5)[0]
    assert "the block's minimum is 0" in check(scrim=-1)[0]
    assert "must be one of none, warm" in check(grade="sepia")[0]
    assert check(grade="warm") == []


def test_a_component_that_is_not_a_block_is_refused(tmp_path):
    stand_ins = fake_registry(tmp_path)
    helper = {
        "name": "helper",
        "type": "hyperframes:component",
        "title": "helper",
        "description": "a snippet",
        "license": "Apache-2.0",
        "files": [
            {
                "path": "blocks/credit-line.html",
                "target": "snippets/helper.html",
                "type": "hyperframes:snippet",
            }
        ],
        "registryDependencies": [],
        "variables": [],
        "source": None,
        "notes": "",
    }
    only_helper = registry.parse_registry({"name": "x", "items": [helper]}, stand_ins.root)
    reg = dataclasses.replace(stand_ins, items={**stand_ins.items, "helper": only_helper.items["helper"]})
    raw = fake_shotlist()
    raw["shots"][0]["layers"][1]["component"] = "helper"

    [message] = problems(raw, reg)

    assert "shot 'p1', layer 2 (helper): 'helper' is a component, not a block" in message


def test_the_photo_shot_stand_in_takes_a_path_and_a_scrim_like_the_brief_says(tmp_path):
    reg = fake_registry(tmp_path)
    raw = fake_shotlist()

    assert problems(raw, reg) == []
    del raw["shots"][0]["layers"][0]["variables"]["to"]
    assert "shot 'p1', layer 1 (photo-shot), variable 'to' (string): not given" in problems(raw, reg)[0]


def test_the_real_video_shot_block_checks_its_variables_against_its_declaration():
    def check(**changes):
        variables = {
            "video": "clips/shot.mp4",
            "trimStart": 0,
            "grade": "{}",
            "scrim": "bottom",
            "hold": "freeze",
            **changes,
        }
        raw = fake_shotlist()
        raw["shots"] = raw["shots"][:1]  # the stand-in map of the second shot is not in the real registry
        raw["shots"][0]["layers"] = [{"component": "video-shot", "at": 0, "variables": variables}]
        return problems(raw)

    assert check() == []
    assert check(hold="loop", trimStart=2.5) == []
    assert "the block's minimum is 0" in check(trimStart=-1)[0]
    assert "the block's maximum is 600" in check(trimStart=601)[0]
    assert "must be a number" in check(trimStart="two")[0]
    assert "must be one of freeze, loop" in check(hold="stop")[0]
    assert "must be one of none, left, bottom" in check(scrim="top")[0]
    assert "is 201 characters, the block takes at most 200" in check(video="v" * 201)[0]


def test_the_real_video_shot_block_makes_the_shot_list_write_every_value_out():
    raw = fake_shotlist()
    raw["shots"] = raw["shots"][:1]
    raw["shots"][0]["layers"] = [{"component": "video-shot", "at": 0, "variables": {"video": "clips/shot.mp4"}}]

    messages = problems(raw)

    # the block's defaults are samples: the enums may default, the strings and numbers may not
    assert [m.split(":")[0] for m in messages] == [
        "shot 'p1', layer 1 (video-shot), variable 'trimStart' (number)",
        "shot 'p1', layer 1 (video-shot), variable 'grade' (string)",
    ]
