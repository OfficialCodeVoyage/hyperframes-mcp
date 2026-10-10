"""The composer: resolved times and slots, every timeline rule, the root page it writes, assets and audio."""

from __future__ import annotations

import dataclasses
import json
import re
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path

import pytest

from hyperframes_mcp.motion import compose as composer
from hyperframes_mcp.motion import lint, registry, tokens
from hyperframes_mcp.motion.compose import compose, duck_lane, index_html, plan
from hyperframes_mcp.motion.shotlist import ShotListError, parse_shotlist
from hyperframes_mcp.motion.timing import Word
from tests.motion.composer_helpers import (
    clip_layer,
    credit_layer,
    edited,
    fake_registry,
    fake_shotlist,
    map_layer,
    photo_layer,
    sample_shotlist,
)

VOICE = 18.0


class Page(HTMLParser):
    """Every element of a page as ``(tag, attributes)``, in order."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, {k: v or "" for k, v in attrs}))

    def with_attr(self, name: str) -> list[dict[str, str]]:
        return [attrs for _, attrs in self.elements if name in attrs]

    def by_id(self, element_id: str) -> dict[str, str]:
        return next(a for _, a in self.elements if a.get("id") == element_id)


@pytest.fixture
def assets(tmp_path) -> Path:
    folder = tmp_path / "assets"
    for name in (
        "audio/voice.wav",
        "audio/bed.wav",
        "audio/whoosh.wav",
        "photos/mine.jpg",
        "clips/shot.mp4",
    ):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(name.encode())
    return folder


def make_plan(
    raw,
    assets,
    *,
    voice=VOICE,
    alignment=None,
    reg=None,
    clip_seconds=None,
    sound_seconds=None,
):
    alignment = SPEECH if alignment is None else alignment
    return plan(
        parse_shotlist(raw),
        list(alignment),
        voice,
        assets_dir=assets,
        registry=reg,
        clip_seconds=clip_seconds,
        sound_seconds=sound_seconds,
    )


def problems_of(raw, assets, **kwargs) -> list[str]:
    with pytest.raises(ShotListError) as error:
        make_plan(raw, assets, **kwargs)
    return error.value.problems


def spoken(text: str, step: float = 0.5) -> list[Word]:
    return [Word(w, round(i * step, 3), round(i * step + step * 0.8, 3)) for i, w in enumerate(text.split())]


SPEECH = spoken("one two three four five six seven eight")


# -- times and slots ----------------------------------------------------------------------------


def test_every_shot_and_layer_gets_its_resolved_start_end_and_slot(assets):
    result = make_plan(sample_shotlist(), assets)

    # s01 stays on screen under the dissolve of s02 (5.5 + 0.5), so it ends at 6.0
    assert [(s.id, s.start, s.end, s.dissolve_s) for s in result.shots] == [
        ("s01", 0.0, 6.0, 0.0),
        ("s02", 5.5, 12.0, 0.5),
        ("s03", 12.0, 18.0, 0.0),
    ]
    got = [(l.shot, l.component, l.start, l.end, l.slot) for l in result.layers]
    assert got == [
        ("s01", "act-title", 0.0, 6.0, 6.0),
        ("s01", "lower-third", 0.3, 6.0, 5.7),
        ("s02", "ledger", 5.5, 12.0, 6.5),
        ("s02", "lower-third", 5.7, 12.0, 6.3),
        ("s03", "act-title", 12.0, 18.0, 6.0),
    ]
    assert result.total_s == 18.0
    assert result.warnings == []


def test_a_dissolve_whose_outgoing_shot_already_runs_under_it_is_the_same(assets):
    raw = edited(sample_shotlist(), (("shots", 0, "duration"), 6.0))
    declared_to_start = edited(sample_shotlist(), (("shots", 0, "duration"), 5.5))

    # ends at the dissolve's start, or at its end: both are extended to the end of the dissolve
    for variant in (raw, declared_to_start):
        assert make_plan(variant, assets).shots[0].end == 6.0


def test_times_may_be_cues_resolved_against_the_narration(assets):
    alignment = [
        Word("one", 0.0, 0.4),
        Word("ledger", 6.0, 6.5),
        Word("closing", 12.0, 12.5),
        Word("titles", 12.6, 13.0),
    ]
    raw = edited(
        sample_shotlist(),
        (("shots", 0, "duration"), {"cue": "ledger"}),
        (("shots", 1, "start"), {"cue": "ledger", "offset": -0.5}),
        (("shots", 1, "duration"), {"cue": "closing"}),
        (("shots", 2, "start"), {"cue": "Closing", "occurrence": 1}),
        (("shots", 2, "duration"), 6),
    )

    result = make_plan(raw, assets, alignment=alignment, voice=30)

    # a cue as a duration is the moment the shot ends
    assert [(s.id, s.start, s.end) for s in result.shots] == [
        ("s01", 0.0, 6.0),
        ("s02", 5.5, 12.0),
        ("s03", 12.0, 18.0),
    ]


def test_a_cue_with_no_alignment_or_not_in_it_is_an_error_that_names_the_shot(assets):
    raw = edited(sample_shotlist(), (("shots", 1, "start"), {"cue": "ledger"}))

    [message] = problems_of(raw, assets, alignment=())
    assert "shot 's02', start: the cue 'ledger' needs the narration's word timings" in message

    [message] = problems_of(raw, assets, alignment=spoken("nothing like it here"))
    assert message.startswith("shot 's02', start: cue 'ledger': it is not in the narration")


def test_a_layer_starts_at_an_offset_and_may_have_its_own_length(assets):
    raw = sample_shotlist()
    raw["shots"][2]["duration"] = 10
    raw["shots"][2]["layers"][0].update({"at": 1.0, "duration": 6.5})

    layer = make_plan(raw, assets).layers[-1]

    assert (layer.start, layer.end, layer.slot) == (13.0, 19.5, 6.5)


def test_a_layer_that_ends_after_its_shot_is_a_warning(assets):
    raw = sample_shotlist()
    raw["shots"][2]["layers"][0]["duration"] = 8

    result = make_plan(raw, assets)

    assert result.warnings == ["shot 's03', layer 1 (act-title) ends at 20.000 s, after its shot (18.000 s)"]


def test_a_slot_below_the_blocks_minimum_is_refused_and_the_minimum_passes(assets):
    raw = sample_shotlist()
    raw["shots"][2]["layers"][0]["duration"] = 4.4  # act-title needs 4.5

    [message] = problems_of(raw, assets)
    assert message.startswith(
        "shot 's03', layer 1 (act-title): a slot of 4.4 s is shorter than the 4.5 s act-title needs"
    )

    # 4.5 is the floor of the static minimum, for a one-word title; "The End" is two words, which the block staggers
    # (0.1 s), so the block's own minimum is 4.55 s and the preflight says so (the page would have thrown)
    raw["shots"][2]["layers"][0]["duration"] = 4.5
    [message] = problems_of(raw, assets)
    assert message.startswith(
        "shot 's03', layer 1 (act-title): a slot of 4.5 s is shorter than the 4.55 s act-title needs"
    )

    raw["shots"][2]["layers"][0]["variables"]["title"] = "End"
    assert make_plan(raw, assets).layers[-1].slot == 4.5


def test_a_slot_over_the_blocks_maximum_is_a_warning(assets):
    raw = sample_shotlist()
    raw["shots"][2]["duration"] = 20  # act-title declares at most 14

    result = make_plan(raw, assets, voice=40)

    assert any("longer than the 14 s act-title declares as its maximum" in w for w in result.warnings)


def test_a_layer_that_starts_after_its_shot_has_ended_is_refused(assets):
    raw = sample_shotlist()
    raw["shots"][2]["layers"][0]["at"] = 6

    [message] = problems_of(raw, assets)

    assert "shot 's03', layer 1 (act-title): 'at' 6 s is at the end of the shot" in message


def test_a_shot_with_no_length_is_refused(assets):
    raw = edited(sample_shotlist(), (("shots", 2, "duration"), 0.0))

    assert "a shot needs a length" in problems_of(raw, assets)[0]


# -- overlaps, gaps and dissolves --------------------------------------------------------------------


def test_shots_overlap_only_through_a_dissolve(assets):
    raw = edited(
        sample_shotlist(),
        (("shots", 1, "transition_in"), {"type": "cut", "seconds": 0}),
    )

    [message] = problems_of(raw, assets)
    assert "shot 's02' starts at 5.500 s while shot 's01' runs to 6.000 s" in message
    assert "overlap only through a dissolve" in message

    raw["shots"][1]["start"] = 6.0  # touching shots are a clean cut
    raw["shots"][1]["duration"] = 6.0
    assert make_plan(raw, assets).warnings == []


def test_a_dissolve_needs_the_shot_before_it_to_reach_it_and_not_to_outlast_it(assets):
    early = edited(sample_shotlist(), (("shots", 0, "duration"), 5.0))
    messages = problems_of(early, assets)
    assert (
        "shot 's02' dissolves in over 0.5 s from 5.500 s, so shot 's01' must end "
        "between 5.500 and 6.000 s; it ends at 5.000 s"
    ) in messages[0]

    late = edited(sample_shotlist(), (("shots", 0, "duration"), 6.4))
    assert "it ends at 6.400 s" in problems_of(late, assets)[0]


def test_a_dissolve_has_the_token_length_and_fits_inside_its_shot(assets):
    for seconds in (0.2, 1.5):
        raw = edited(
            sample_shotlist(),
            (("shots", 1, "transition_in"), {"type": "dissolve", "seconds": seconds}),
        )
        raw["shots"][0]["duration"] = 5.5 + seconds
        assert "a dissolve lasts 0.5 to 1 s (the tokens)" in problems_of(raw, assets)[0]
    short = sample_shotlist()
    short["shots"][2]["duration"] = 0.3
    short["shots"][2]["transition_in"] = {"type": "dissolve", "seconds": 0.5}
    short["shots"][2]["layers"][0]["duration"] = 0.3
    assert any("shorter than its own 0.5 s dissolve" in m for m in problems_of(short, assets))


def test_a_cut_has_no_length(assets):
    raw = edited(
        sample_shotlist(),
        (("shots", 2, "transition_in"), {"type": "cut", "seconds": 0.5}),
    )

    assert "a cut has no length" in problems_of(raw, assets)[0]


def test_shots_must_be_listed_in_time_order(assets):
    raw = sample_shotlist()
    raw["shots"][1], raw["shots"][2] = raw["shots"][2], raw["shots"][1]

    assert any("list the shots in time order" in m for m in problems_of(raw, assets))


def test_a_gap_between_cut_shots_is_a_warning_and_a_late_first_shot_too(assets):
    raw = sample_shotlist()
    raw["shots"][0].update({"start": 0.5, "duration": 6.0})  # runs to 6.5
    raw["shots"][1].update({"start": 6.0, "duration": 6.0})  # dissolves in 6.0 to 6.5
    raw["shots"][2]["start"] = 13  # 12.0 to 13.0 is a gap

    result = make_plan(raw, assets)

    assert result.warnings == [
        "the first shot starts at 0.500 s: the night ground shows before it",
        "1.00 s gap between shot 's02' (ends 12.000 s) and shot 's03' (starts 13.000 s): the night ground shows",
    ]


def test_the_first_shot_may_dissolve_in_from_the_ground(assets):
    raw = edited(
        sample_shotlist(),
        (("shots", 0, "transition_in"), {"type": "dissolve", "seconds": 0.5}),
    )

    result = make_plan(raw, assets)
    html = index_html(result)

    assert result.shots[0].dissolve_s == 0.5
    assert html.count("tl.fromTo(") == 3  # in for s01, in for s02 plus out for s01 from the s02 dissolve
    assert 'tl.fromTo(".shot-s01", { opacity: 0 }' in html


# -- the narration's length --------------------------------------------------------------------------


def test_times_must_be_inside_the_voice_when_its_length_is_known(assets):
    raw = sample_shotlist()

    assert problems_of(raw, assets, voice=11.0) == ["shot 's03' starts at 12.000 s, after the voice ends at 11.000 s"]
    assert make_plan(raw, assets, voice=None).total_s == 18.0
    assert make_plan(raw, assets, voice=12.5).total_s == 18.0


def test_a_sound_effect_after_the_voice_is_refused_and_the_voice_past_the_picture_warned(
    assets,
):
    raw = edited(sample_shotlist(), (("audio", "sfx", 0, "at"), 19.0))
    assert "audio, sfx 1: at 19.000 s is after the voice ends at 18.500 s" in problems_of(raw, assets, voice=18.5)

    result = make_plan(sample_shotlist(), assets, voice=19.5)
    assert result.total_s == 19.5
    assert any("the voice runs 1.50 s past the last shot" in w for w in result.warnings)


# -- provenance of archival photos (stand-in blocks) ---------------------------------------------


def with_photo(**overrides):
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [photo_layer(), credit_layer(**overrides)]
    return raw


def test_a_photo_needs_a_credit_line_with_text_that_is_on_screen_with_it(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    passing = make_plan(with_photo(), assets, reg=reg)
    assert [l.component for l in passing.layers[:2]] == ["photo-shot", "credit-line"]

    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [photo_layer()]
    [message] = problems_of(raw, assets, reg=reg)
    assert message.startswith("shot 'p1', layer 1 (photo-shot): photo-shot needs a credit-line layer")

    # a credit with no text (the empty string is a legal value for the block, not for provenance)
    assert "needs a credit-line layer" in problems_of(with_photo(variables={"text": "   "}), assets, reg=reg)[0]


def test_a_credit_line_in_another_shot_or_at_another_time_does_not_count(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    other_shot = fake_shotlist()
    other_shot["shots"][0]["layers"] = [photo_layer()]
    other_shot["shots"][1]["layers"] = [map_layer(), credit_layer()]
    assert "needs a credit-line layer" in problems_of(other_shot, assets, reg=reg)[0]

    later = fake_shotlist()
    later["shots"][0]["layers"] = [
        photo_layer(duration=3),
        credit_layer(at=3.5, duration=4),
    ]
    assert "needs a credit-line layer" in problems_of(later, assets, reg=reg)[0]

    overlapping = fake_shotlist()
    overlapping["shots"][0]["layers"] = [
        photo_layer(duration=3),
        credit_layer(at=2.5, duration=4),
    ]
    assert make_plan(overlapping, assets, reg=reg).layers  # half a second of overlap is enough


def test_a_credit_layer_that_opts_into_a_sample_has_no_text_and_does_not_count(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [
        photo_layer(),
        {"component": "credit-line", "at": 0.3, "sample": True, "variables": {}},
    ]

    assert "needs a credit-line layer" in problems_of(raw, assets, reg=reg)[0]


def test_a_map_needs_no_credit_layer_by_the_composer(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")

    result = make_plan(fake_shotlist(), assets, reg=reg)

    assert [l.component for l in result.layers] == [
        "photo-shot",
        "credit-line",
        "map-route",
    ]
    assert result.layers[2].values["region"] == "Germany"


# -- video clips (stand-in blocks) ---------------------------------------------------------------


def with_clip(clip=None, **credit):
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [clip_layer(**(clip or {})), credit_layer(**credit)]
    return raw


def clip_of(seconds):
    """A probe that finds every clip ``seconds`` long."""
    return lambda path: seconds


def test_a_clip_needs_a_credit_line_with_text_that_is_on_screen_with_it(tmp_path, assets):
    """The rule reads the registry flag needsCredit, so a clip is held to it exactly like a photo."""
    reg = fake_registry(tmp_path / "reg")
    passing = make_plan(with_clip(), assets, reg=reg)
    assert [l.component for l in passing.layers[:2]] == ["video-shot", "credit-line"]

    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [clip_layer()]
    [message] = problems_of(raw, assets, reg=reg)
    assert message.startswith("shot 'p1', layer 1 (video-shot): video-shot needs a credit-line layer")

    # a credit with no text, in another shot, or at another time does not count
    assert "needs a credit-line layer" in problems_of(with_clip(variables={"text": "   "}), assets, reg=reg)[0]
    other_shot = fake_shotlist()
    other_shot["shots"][0]["layers"] = [clip_layer()]
    other_shot["shots"][1]["layers"] = [map_layer(), credit_layer()]
    assert "needs a credit-line layer" in problems_of(other_shot, assets, reg=reg)[0]
    later = fake_shotlist()
    later["shots"][0]["layers"] = [
        clip_layer(duration=3),
        credit_layer(at=3.5, duration=4),
    ]
    assert "needs a credit-line layer" in problems_of(later, assets, reg=reg)[0]


def test_the_credit_rule_follows_the_flag_not_the_block_name(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [clip_layer(), credit_layer()]
    assert make_plan(raw, assets, reg=reg).layers

    flagless = dataclasses.replace(
        reg,
        items={
            **reg.items,
            "video-shot": dataclasses.replace(reg.items["video-shot"], needs_credit=False),
        },
    )
    raw["shots"][0]["layers"] = [clip_layer()]
    # without the flag a clip alone is fine, and the photo next to it is still held to the rule
    assert make_plan(raw, assets, reg=flagless).layers
    raw["shots"][0]["layers"] = [photo_layer()]
    assert "needs a credit-line layer" in problems_of(raw, assets, reg=flagless)[0]


def test_the_video_a_layer_names_is_copied_and_the_variable_points_into_the_project(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    project = tmp_path / "project"

    result = compose(
        parse_shotlist(with_clip()),
        [],
        VOICE,
        project,
        assets_dir=assets,
        registry=reg,
    )

    assert (project / "assets/media/clips/shot.mp4").read_bytes() == b"clips/shot.mp4"
    page = Page((project / "index.html").read_text(encoding="utf-8"))
    values = json.loads(page.by_id("l-p1-1")["data-variable-values"])
    assert values["video"] == "assets/media/clips/shot.mp4"
    assert values["trimStart"] == 0 and values["hold"] == "freeze"
    assert "assets/media/clips/shot.mp4" in result.files
    # only the voice is ever mixed: a clip is never an audio element, whatever sound the file has
    assert [a["id"] for a in page.with_attr("data-audio-group")] == ["voice"]


@pytest.mark.parametrize(
    "path, fragment",
    [
        ("../outside.mp4", "'..' segment"),
        ("clips/missing.mp4", "is not a file in the assets folder"),
    ],
)  # the other refusals are the shared asset check, tested above for an image
def test_a_video_path_that_leaves_the_assets_folder_or_is_not_a_file_is_refused(tmp_path, assets, path, fragment):
    reg = fake_registry(tmp_path / "reg")
    raw = with_clip(clip={"variables": {"video": path}})

    [message] = problems_of(raw, assets, reg=reg)

    assert message.startswith("shot 'p1', layer 1 (video-shot), variable 'video':")
    assert fragment in message


def test_an_empty_video_copies_nothing_and_is_not_probed(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = with_clip(clip={"variables": {"video": ""}})

    def refuse(path):
        raise AssertionError("an empty path has no file to probe")

    result = make_plan(raw, assets, reg=reg, clip_seconds=refuse)

    assert [a.target for a in result.assets] == ["assets/media/audio/voice.wav"]


def test_a_slot_longer_than_the_clip_warns_and_names_what_the_hold_does(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    frozen = make_plan(with_clip(), assets, reg=reg, clip_seconds=clip_of(5.0))
    looped = make_plan(
        with_clip(clip={"variables": {"hold": "loop", "trimStart": 1}}),
        assets,
        reg=reg,
        clip_seconds=clip_of(5.0),
    )

    # the first shot is 8 s long: 3 s past the 5 s clip, the last frame stays
    assert (
        "shot 'p1', layer 1 (video-shot): the slot is 8 s but the clip is only 5 s long after "
        "trimStart 0 s; hold is freeze, so its last frame stays on screen for the last 3 s"
    ) in frozen.warnings
    # trimmed by 1 s the clip has 4 s: it plays through twice
    assert (
        "shot 'p1', layer 1 (video-shot): the slot is 8 s but the clip is only 4 s long after "
        "trimStart 1 s; hold is loop, so it plays again from trimStart, 2 times in all"
    ) in looped.warnings


def test_a_loop_that_ends_part_way_says_so(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")

    result = make_plan(
        with_clip(clip={"variables": {"hold": "loop"}}),
        assets,
        reg=reg,
        clip_seconds=clip_of(6.0),
    )

    assert any("2 times in all (the last one is cut" in w for w in result.warnings)


def test_a_clip_that_fills_the_slot_gives_no_warning(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")

    for seconds in (
        8.0,
        7.97,
        12.0,
    ):  # up to a frame (1/24 s) short of the slot is not a gap
        result = make_plan(with_clip(), assets, reg=reg, clip_seconds=clip_of(seconds))
        assert [w for w in result.warnings if "clip" in w] == [], seconds
    long_enough = make_plan(
        with_clip(clip={"variables": {"trimStart": 2}}),
        assets,
        reg=reg,
        clip_seconds=clip_of(10.0),
    )
    assert [w for w in long_enough.warnings if "clip" in w] == []


def test_a_trim_start_at_or_beyond_the_end_of_the_clip_is_an_error(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    for trim in (5.5, 5.0):
        raw = with_clip(clip={"variables": {"trimStart": trim}})

        [message] = problems_of(raw, assets, reg=reg, clip_seconds=clip_of(5.0))

        assert message == (
            f"shot 'p1', layer 1 (video-shot), variable 'trimStart': {trim:g} s is not before the end "
            "of the clip, which is 5.00 s long"
        )
    just_inside = with_clip(clip={"variables": {"trimStart": 4.5}})
    assert make_plan(just_inside, assets, reg=reg, clip_seconds=clip_of(5.0)).layers


def test_a_clip_that_cannot_be_read_is_an_error_naming_the_layer(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")

    def unreadable(path):
        raise ValueError("it has no video stream")

    [message] = problems_of(with_clip(), assets, reg=reg, clip_seconds=unreadable)

    assert message == (
        "shot 'p1', layer 1 (video-shot), variable 'video': the clip cannot be read: it has no video stream"
    )


def test_without_a_probe_the_clip_is_not_checked(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = with_clip(clip={"variables": {"trimStart": 500}})

    result = make_plan(raw, assets, reg=reg)  # no ffprobe: like an unknown voice length

    assert [w for w in result.warnings if "clip" in w] == []


def test_the_probe_gets_the_absolute_source_path_of_each_layers_clip(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [
        clip_layer(duration=3),
        clip_layer(at=3, duration=3),
        credit_layer(),
    ]
    seen = []

    def probe(path):
        seen.append(path)
        return 10.0

    make_plan(raw, assets, reg=reg, clip_seconds=probe)

    assert len(seen) == 2 and {p.name for p in seen} == {"shot.mp4"}
    assert all(p.is_absolute() and p.is_relative_to(assets.resolve()) for p in seen)


# -- assets ----------------------------------------------------------------------------------------------


def test_the_image_a_layer_names_is_copied_and_the_variable_points_into_the_project(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    project = tmp_path / "project"

    result = compose(
        parse_shotlist(with_photo()),
        [],
        VOICE,
        project,
        assets_dir=assets,
        registry=reg,
    )

    assert (project / "assets/media/photos/mine.jpg").read_bytes() == b"photos/mine.jpg"
    page = Page((project / "index.html").read_text(encoding="utf-8"))
    values = json.loads(page.by_id("l-p1-1")["data-variable-values"])
    assert values["image"] == "assets/media/photos/mine.jpg"
    assert "assets/media/photos/mine.jpg" in result.files
    assert "assets/media/audio/voice.wav" in result.files


@pytest.mark.parametrize(
    "path, fragment",
    [
        ("/etc/passwd", "relative path"),
        ("../outside.jpg", "'..' segment"),
        ("photos/../../outside.jpg", "'..' segment"),
        ("https://example.com/a.jpg", "URL"),
        ("//example.com/a.jpg", "URL"),
        ("file:///etc/passwd", "URL"),
        ("data:image/png;base64,AAAA", "URL"),
        ("C:\\photos\\a.jpg", "URL or a drive path"),
        ("photos\\a.jpg", "forward slashes"),
        ("photos//a.jpg", "empty"),
        ("photos/missing.jpg", "is not a file in the assets folder"),
        ("photos", "is not a file"),
    ],
)
def test_an_image_path_that_leaves_the_assets_folder_or_is_not_a_file_is_refused(tmp_path, assets, path, fragment):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"][0] = photo_layer(variables={"image": path})

    [message] = problems_of(raw, assets, reg=reg)

    assert message.startswith("shot 'p1', layer 1 (photo-shot), variable 'image':")
    assert fragment in message


def test_a_link_that_points_out_of_the_assets_folder_is_refused(tmp_path, assets):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"secret")
    (assets / "photos" / "link.jpg").symlink_to(secret)
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"][0] = photo_layer(variables={"image": "photos/link.jpg"})

    [message] = problems_of(raw, assets, reg=reg)

    assert "leads outside the assets folder" in message


def test_an_empty_image_is_no_image_and_copies_nothing(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"][0] = photo_layer(variables={"image": ""})

    result = make_plan(raw, assets, reg=reg)

    assert [a.target for a in result.assets] == ["assets/media/audio/voice.wav"]


def test_audio_paths_are_held_to_the_same_rules(tmp_path, assets):
    for bad in ("../voice.wav", "/abs/voice.wav", "http://x/voice.wav"):
        with pytest.raises(ShotListError, match="audio, voice, path"):
            parse_shotlist(edited(sample_shotlist(), (("audio", "voice", "path"), bad)))
    raw = edited(sample_shotlist(), (("audio", "music", "path"), "audio/nope.wav"))
    assert "audio, music: 'audio/nope.wav' is not a file in the assets folder" in problems_of(raw, assets)[0]


# -- the page ----------------------------------------------------------------------------------------------


def test_the_root_page_mounts_every_layer_with_the_slot_as_duration_and_variable(tmp_path, assets):
    result = compose(parse_shotlist(sample_shotlist()), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")
    page = Page(text)

    root = page.by_id("root")
    assert root["data-composition-id"] == "main"
    assert (root["data-width"], root["data-height"], root["data-fps"]) == (
        "1920",
        "1080",
        "24",
    )
    assert float(root["data-duration"]) == 18.0
    mounts = [a for a in page.with_attr("data-composition-src")]
    assert len(mounts) == len(result.layers) == 5
    for mount, layer in zip(mounts, result.layers, strict=True):
        values = json.loads(mount["data-variable-values"])
        assert float(mount["data-duration"]) == layer.slot == values["durationSeconds"]
        assert float(mount["data-start"]) == layer.start
        assert mount["data-composition-src"] == f"compositions/{layer.component}.html"
        assert mount["data-composition-id"] == layer.component
        assert mount["class"] == f"clip shot-{layer.shot}"
        assert (mount["data-width"], mount["data-height"]) == ("1920", "1080")
    # what the shot supplied is carried, and nothing it did not supply
    first = json.loads(mounts[1]["data-variable-values"])
    assert first == {
        "durationSeconds": 5.7,
        "name": "Jay Cooke",
        "role": "Financier, 1821-1905",
        "side": "left",
    }


def test_a_block_mounted_twice_is_installed_once_and_each_mount_has_its_own_id_and_slot(tmp_path, assets):
    result = compose(parse_shotlist(sample_shotlist()), [], VOICE, tmp_path / "p", assets_dir=assets)
    page = Page((tmp_path / "p" / "index.html").read_text(encoding="utf-8"))

    lower_thirds = [a for a in page.with_attr("data-composition-src") if a["data-composition-id"] == "lower-third"]

    assert [a["id"] for a in lower_thirds] == ["l-s01-2", "l-s02-2"]
    assert [a["data-duration"] for a in lower_thirds] == ["5.7", "6.3"]
    assert result.files.count("compositions/lower-third.html") == 1
    ids = [a["id"] for _, a in page.elements if "id" in a]
    assert len(ids) == len(set(ids))


def test_the_layers_sit_side_by_side_in_the_root_with_later_shots_in_front(tmp_path, assets):
    raw = sample_shotlist()
    # the act title above s01's lower third, yet under every layer of s02
    raw["shots"][0]["layers"][0]["track"] = 5
    compose(parse_shotlist(raw), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")
    page = Page(text)

    # no wrapper: Studio draws an untimed child of the root as one clip the whole video long
    assert all("data-start" in a for tag, a in page.elements if tag == "div")
    assert 'id="ground"' not in text
    assert tokens.default_tokens().colors["night"] in text
    z = {a["id"]: int(a["style"].removeprefix("z-index: ")) for a in page.with_attr("data-composition-src")}
    assert sorted(z, key=z.get) == [
        "l-s01-2",
        "l-s01-1",
        "l-s02-1",
        "l-s02-2",
        "l-s03-1",
    ]


def test_a_dissolve_is_a_crossfade_of_the_layers_of_two_shots_on_the_token_eases(tmp_path, assets):
    compose(parse_shotlist(sample_shotlist()), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")
    ease = tokens.default_tokens().ease

    tweens = re.findall(r"tl\.fromTo\((.*)\);", text)
    assert len(tweens) == 2
    assert tweens[0] == (
        f'".shot-s02", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.5, ease: "{ease["enter"]}" }}, 5.5'
    )
    assert tweens[1] == (
        f'".shot-s01", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.5, ease: "{ease["exit"]}", '
        "immediateRender: false }, 5.5"
    )
    assert "gsap.timeline({ paused: true })" in text
    assert 'window.__timelines["main"] = tl;' in text
    # opacity of the layer mounts only: nothing in the page touches a picture inside a block
    assert not re.search(r"\.(?:to|from)\(", text)


def test_a_hard_cut_is_the_default_and_adds_no_tween(tmp_path, assets):
    raw = sample_shotlist()
    raw["shots"][1].update({"transition_in": {"type": "cut"}, "start": 6.0, "duration": 6.0})

    compose(parse_shotlist(raw), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")

    assert "tl.fromTo" not in text
    assert 'window.__timelines["main"] = tl;' in text


def test_the_audio_elements_are_the_voice_the_music_bed_and_the_sound_effects(tmp_path, assets):
    text_of_voice = "In the mine the gold was counted. Then silence. The end."
    alignment = spoken(text_of_voice)
    compose(
        parse_shotlist(sample_shotlist()),
        alignment,
        VOICE,
        tmp_path / "p",
        assets_dir=assets,
    )
    page = Page((tmp_path / "p" / "index.html").read_text(encoding="utf-8"))

    voice = page.by_id("voice")
    assert voice["src"] == "assets/media/audio/voice.wav"
    assert (voice["data-start"], voice["data-duration"], voice["data-volume"]) == (
        "0",
        "18.0",
        "1",
    )
    music = page.by_id("music")
    assert music["src"] == "assets/media/audio/bed.wav"
    assert music["data-volume"] == repr(round(10 ** (-18 / 20), 4))
    assert float(music["data-duration"]) == 18.0
    lane = json.loads(music["data-automation"])["lanes"][0]
    assert lane["target"] == "volume"
    assert min(p["v"] for p in lane["points"]) == round(10 ** (-9 / 20), 4)
    sfx = page.by_id("sfx-1")
    assert (sfx["src"], sfx["data-start"], sfx["data-volume"]) == (
        "assets/media/audio/whoosh.wav",
        "5.5",
        repr(round(10 ** (-6 / 20), 4)),
    )
    tracks = [a["data-track-index"] for t, a in page.elements if t == "audio"]
    assert len(tracks) == len(set(tracks))
    assert {a["data-audio-group"] for t, a in page.elements if t == "audio"} == {
        "voiceover",
        "music",
        "sfx",
    }


def _span(mount: dict[str, str]) -> tuple[float, float]:
    start = float(mount["data-start"])
    return start, start + float(mount["data-duration"])


def test_studio_rows_hold_one_kind_each_with_no_two_clips_on_a_row_at_once(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"].append(credit_layer(at=2, variables={"text": "A second credit"}))
    raw["shots"].insert(
        1,
        {
            "id": "p2",
            "start": 7.5,
            "duration": 4,
            "transition_in": {"type": "dissolve", "seconds": 0.5},
            "layers": [photo_layer(), credit_layer(at=0)],
        },
    )
    raw["shots"][2].update(start=11.5, duration=6)
    page = Page(index_html(make_plan(raw, assets, reg=reg)))
    mounts = page.with_attr("data-composition-src")
    kinds = {
        "photo-shot": "Pictures",
        "map-route": "Graphics",
        "credit-line": "Labels",
    }
    rows: dict[str, set[int]] = {}
    for mount in mounts:
        rows.setdefault(kinds[mount["data-composition-id"]], set()).add(int(mount["data-track-index"]))

    # the photos dissolve into each other, so two picture rows; the three credits overlap, so three label rows
    assert {kind: len(found) for kind, found in rows.items()} == {
        "Pictures": 2,
        "Graphics": 1,
        "Labels": 3,
    }
    assert max(rows["Pictures"]) < min(rows["Graphics"])
    assert max(rows["Graphics"]) < min(rows["Labels"])
    by_row: dict[str, list[tuple[float, float]]] = {}
    for mount in mounts:
        by_row.setdefault(mount["data-track-index"], []).append(_span(mount))
    for spans in by_row.values():
        spans.sort()
        assert all(a[1] <= b[0] + 1e-9 for a, b in pairwise(spans))


def test_every_clip_carries_a_studio_label_with_its_shot_kind_and_words(tmp_path, assets):
    reg = fake_registry(tmp_path / "reg")
    raw = fake_shotlist()
    raw["shots"][0]["layers"][1]["variables"]["text"] = (
        "Library of Congress, Prints and Photographs Division, a very long credit"
    )
    page = Page(index_html(make_plan(raw, assets, reg=reg)))

    labels = [a["data-timeline-label"] for a in page.with_attr("data-composition-src")]

    assert labels == [
        "p1 · Photo: mine",
        "p1 · Credit: Library of Congress, Prints and Photographs Div…",
        "m1 · Map: Germany",
    ]
    assert page.by_id("voice")["data-timeline-label"] == "Voice"


def test_sound_effects_share_a_row_unless_they_sound_at_once(assets):
    raw = sample_shotlist()
    raw["audio"]["sfx"] = [{"path": "audio/whoosh.wav", "at": at, "gain_db": -6.0} for at in (1, 1.5, 4)]
    page = Page(index_html(make_plan(raw, assets, sound_seconds=lambda path: 1.0)))
    row = {a["id"]: int(a["data-track-index"]) for t, a in page.elements if t == "audio"}

    assert row["sfx-1"] == row["sfx-3"] != row["sfx-2"]
    assert row["voice"] < row["music"] < min(row["sfx-1"], row["sfx-2"])
    assert page.by_id("sfx-2")["data-timeline-label"] == "SFX: whoosh"


def test_a_sound_effect_whose_length_is_unknown_keeps_a_row_of_its_own(assets):
    raw = sample_shotlist()
    raw["audio"]["sfx"] = [{"path": "audio/whoosh.wav", "at": at, "gain_db": -6.0} for at in (1, 4)]

    def unreadable(path):
        raise ValueError("it has no audio stream")

    for sound_seconds in (None, unreadable):
        made = make_plan(raw, assets, sound_seconds=sound_seconds)
        page = Page(index_html(made))
        rows = [a["data-track-index"] for t, a in page.elements if t == "audio"]
        assert len(rows) == len(set(rows))
    assert any("sfx 1" in w and "no audio stream" in w for w in made.warnings)


def test_lanes_pack_spans_in_order_of_start_and_answer_in_the_order_given():
    assert composer._lanes([(4, 6), (0, 2), (1, 3), (2, 4)]) == [0, 0, 1, 0]


def test_every_block_in_the_registry_has_a_studio_row_and_its_label_shows_one_of_its_variables():
    reg = registry.default_registry()
    blocks = {name for name, item in reg.items.items() if item.type == "hyperframes:block"}

    assert blocks == set(composer.COMPONENT_ROWS)
    for name, (kind, _, variable) in composer.COMPONENT_ROWS.items():
        assert kind in composer.ROW_KINDS
        assert variable is None or variable in {v["id"] for v in reg.items[name].variables}


def test_a_shot_list_with_no_music_and_an_unknown_voice_length_writes_a_bare_voice(tmp_path, assets):
    raw = sample_shotlist()
    del raw["audio"]["music"], raw["audio"]["sfx"]

    compose(parse_shotlist(raw), [], None, tmp_path / "p", assets_dir=assets)
    page = Page((tmp_path / "p" / "index.html").read_text(encoding="utf-8"))

    assert [a["id"] for t, a in page.elements if t == "audio"] == ["voice"]
    assert "data-duration" not in page.by_id("voice")


def test_a_music_duck_without_an_alignment_is_a_warning(assets):
    assert any("not ducked" in w for w in make_plan(sample_shotlist(), assets, alignment=()).warnings)
    assert not any(
        "not ducked" in w for w in make_plan(sample_shotlist(), assets, alignment=spoken("one two three")).warnings
    )


def test_the_page_has_no_network_address_and_passes_the_host_page_lint_checks(tmp_path, assets):
    compose(parse_shotlist(sample_shotlist()), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")

    assert not re.search(r"https?://|(?:src|href)=[\"']//", text)
    # the library lint, minus the two checks that are about a block file (a host page declares no variables)
    problems = [p for p in lint.lint_text(text, "index.html") if p.code not in {"MISSING_VARIABLES"}]
    assert problems == []


def test_an_identical_shot_list_writes_an_identical_page(tmp_path, assets):
    for name in ("a", "b"):
        compose(
            parse_shotlist(sample_shotlist()),
            [],
            VOICE,
            tmp_path / name,
            assets_dir=assets,
        )

    assert (tmp_path / "a" / "index.html").read_bytes() == (tmp_path / "b" / "index.html").read_bytes()


def test_compose_lists_what_it_wrote_and_installs_each_block_with_its_shared_files(tmp_path, assets):
    result = compose(parse_shotlist(sample_shotlist()), [], VOICE, tmp_path / "p", assets_dir=assets)

    for expected in (
        "index.html",
        "meta.json",
        "tokens.css",
        "vendor/gsap.min.js",
        "motion.lock.json",
        "compositions/act-title.html",
        "compositions/lower-third.html",
        "compositions/ledger.html",
        "assets/media/audio/voice.wav",
        "assets/media/audio/bed.wav",
        "assets/media/audio/whoosh.wav",
    ):
        assert expected in result.files
        assert (tmp_path / "p" / expected).is_file()
    assert "compositions/counter.html" not in result.files  # only the blocks the shot list uses
    assert json.loads((tmp_path / "p" / "meta.json").read_text())["name"] == "Test video"
    assert result.total_s == 18.0 and result.project_dir == tmp_path / "p"


def test_a_shot_list_that_breaks_a_rule_writes_nothing(tmp_path, assets):
    raw = sample_shotlist()
    raw["shots"][2]["layers"][0]["variables"].pop("title")

    with pytest.raises(ShotListError, match="variable 'title'"):
        compose(parse_shotlist(raw), [], VOICE, tmp_path / "p", assets_dir=assets)

    assert not (tmp_path / "p").exists()


def test_every_problem_of_a_shot_list_is_reported_together(assets):
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"].pop("role")
    raw["shots"][2]["layers"][0]["duration"] = 3

    messages = problems_of(raw, assets)

    assert len(messages) == 2
    assert "variable 'role'" in messages[0] and "shorter than the 4.5 s" in messages[1]


def test_the_title_is_escaped_in_the_page(tmp_path, assets):
    raw = edited(sample_shotlist(), (("meta", "title"), "Gold </title><b> & more"))

    compose(parse_shotlist(raw), [], VOICE, tmp_path / "p", assets_dir=assets)
    text = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")

    assert "<title>Gold &lt;/title&gt;&lt;b&gt; &amp; more</title>" in text


# -- the music duck -------------------------------------------------------------------------------------


def lane(alignment, duck_db=-9.0, length=40.0) -> list[tuple[float, float]]:
    raw = duck_lane(alignment, duck_db, length)
    assert raw is not None
    return [(p["t"], p["v"]) for p in json.loads(raw)["lanes"][0]["points"]]


def test_the_duck_falls_before_the_voice_holds_and_returns_slowly():
    depth = round(10 ** (-9 / 20), 4)

    points = lane([Word("a", 2.0, 2.4), Word("b", 2.5, 3.0)])

    assert points == [
        (0.0, 1.0),
        (1.75, 1.0),
        (2.0, depth),
        (3.0, depth),
        (3.8, 1.0),
    ]


def test_a_short_pause_keeps_the_duck_down_and_a_long_one_lets_the_bed_back_up():
    depth = round(10 ** (-9 / 20), 4)

    held = lane([Word("a", 2.0, 2.4), Word("b", 3.0, 3.4)])  # a 0.6 s pause
    assert [v for _, v in held] == [1.0, 1.0, depth, depth, 1.0]

    released = lane([Word("a", 2.0, 2.4), Word("b", 6.0, 6.4)])  # a 3.6 s pause
    assert [v for _, v in released] == [
        1.0,
        1.0,
        depth,
        depth,
        1.0,
        1.0,
        depth,
        depth,
        1.0,
    ]


def test_a_voice_that_starts_at_zero_starts_ducked_and_points_past_the_bed_are_dropped():
    depth = round(10 ** (-9 / 20), 4)

    assert lane([Word("a", 0.0, 1.0)])[0] == (0.0, depth)
    assert lane([Word("a", 0.0, 1.0)], length=1.5)[-1] == (1.0, depth)


def test_no_alignment_or_no_duck_means_no_lane():
    assert duck_lane([], -9.0, 10) is None
    assert duck_lane([Word("a", 0, 1)], 0.0, 10) is None


def test_constants_are_the_ones_the_docs_state():
    assert (composer.DUCK_ATTACK_S, composer.DUCK_RELEASE_S) == (0.25, 0.8)
    assert composer.MEDIA_DIR == "assets/media"
