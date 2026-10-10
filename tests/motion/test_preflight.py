"""The preflight of the composer: the shortest slot of a block, computed by the block's own script, and layers hidden
behind a layer that paints the whole frame.

Both checks run in ``compose.plan`` (so in ``build --check``, ``compose`` and ``build``): an error stops, a warning prints.
The minimum slots are tested three ways: against the hand-derived formulas of the brief (a cross-check, not the
spec), against the block's own script run in node (the block must refuse a slot a hair under the number and accept
the number), and through ``plan`` on small shot lists. The hidden layers are tested on small shot lists, including
the two failures of the 2-minute gold test, rebuilt.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from hyperframes_mcp.motion import preflight, registry, tokens
from hyperframes_mcp.motion.compose import plan
from hyperframes_mcp.motion.shotlist import ShotListError, parse_shotlist
from tests.motion.bar_grid_helpers import COLD_OPEN, FOR_SCALE
from tests.motion.block_harness import rule_of
from tests.motion.block_harness import run_block as run_page
from tests.motion.block_runner import css_properties
from tests.motion.block_runner import run_block as run_runner
from tests.motion.node_logic import LogicError, logic_of, needs_node
from tests.motion.node_logic import run as node_run

REG = registry.default_registry()
BLOCKS = tokens.MOTION_DIR / "blocks"


ONE_LETTER_ROLE = "a b c d e f g h i j k l m n o p q r s t u v w x y z"


def words(*texts: str) -> int:
    return len(" ".join(texts).split())


def need(component: str, **variables: Any) -> float:
    (result,) = preflight.minimum_slots([(component, variables)], REG)
    assert result.error is None, result.error
    return result.seconds


# -- the blocks that compute their own minimum ------------------------------------------------------

PRE_CHECKED = (
    "credit-line",
    "date-stamp",
    "lower-third",
    "act-title",
    "quote-card",
    "counter",
    "ledger",
    "bar-grid",
    "split-bar",
    "compare-bars",
)
# blocks whose only slot rule is the static minimum of durationSeconds (nothing depends on their text)
STATIC_ONLY = ("photo-shot", "video-shot", "finish", "graphic-stamp")


def test_every_block_says_how_its_minimum_slot_is_known():
    """A new block must decide: a minSlot function, a reason it cannot have one, or a static minimum only."""
    for item in registry.list_items(REG):
        if item.type != "hyperframes:block":
            continue
        has_function = preflight.block_logic(item, REG) is not None
        stated = [
            has_function,
            item.slot_not_checked is not None,
            item.name in STATIC_ONLY,
        ]
        assert sum(stated) == 1, f"{item.name}: {stated}"
    assert {i.name for i in registry.list_items(REG) if preflight.block_logic(i, REG)} == set(PRE_CHECKED)


def test_the_blocks_that_are_not_pre_checked_say_why():
    assert "camera" in REG.items["map-route"].slot_not_checked
    assert REG.items["newspaper-clipping"].slot_not_checked


@pytest.mark.parametrize("name", PRE_CHECKED)
def test_the_logic_markers_exist_once_and_hold_no_dom(name):
    text = (BLOCKS / f"{name}.html").read_text(encoding="utf-8")

    assert text.count("// logic:begin") == 1
    assert text.count("// logic:end") == 1
    logic = logic_of(BLOCKS / f"{name}.html")
    assert "function minSlot(V, tok)" in logic
    assert "document" not in logic and "getComputedStyle" not in logic


# -- the hand-derived formulas of the brief, as a cross-check ---------------------------------------


def test_credit_line_is_2_4_plus_3_tenths_a_word():
    for text in (
        "Library of Congress",
        "Library of Congress, Prints and Photographs Division.",
    ):
        assert need("credit-line", text=text) == pytest.approx(2.4 + 0.3 * words(text))


def test_credit_line_counts_words_split_on_single_spaces_as_the_block_does():
    # two spaces make an empty word for the block, which the preflight must share: it runs the block's own text
    assert need("credit-line", text="a  b") == pytest.approx(2.4 + 0.3 * 3)


def test_date_stamp_is_2_7_plus_3_tenths_a_word():
    for text in ("April 12, 1945", "Late February to March 1945"):
        assert need("date-stamp", text=text) == pytest.approx(2.7 + 0.3 * words(text))


def test_lower_third_holds_four_seconds_or_the_reading_time():
    short = need("lower-third", name="Jay Cooke", role="Financier")
    assert short == pytest.approx(1.2 + 4 + 0.5)
    name = "Franklin Delano Roosevelt"
    role = ONE_LETTER_ROLE  # 26 words in the 64 characters the band holds
    long = need("lower-third", name=name, role=role)
    assert long == pytest.approx(1.2 + 0.3 * words(name, role) + 0.5)
    assert long > short


def test_act_title_adds_the_squeezed_word_stagger_and_holds_at_least_2_5_s():
    plain = need("act-title", kicker="Part 1", title="The End", subtitle="")
    assert plain == pytest.approx(1.45 + min(0.5, 0.1 * 1) + 2.5 + 0.5)
    title = "How a railroad boom broke the banks of the old world"
    subtitle = "The long road from the first spike to the last panic and what it left behind"
    long = need("act-title", kicker="Part 2", title=title, subtitle=subtitle)
    n = words(title)
    assert long == pytest.approx(
        1.45 + min(0.5, 0.1 * (n - 1)) + max(2.5, 0.3 * words("Part 2", title, subtitle)) + 0.5
    )


LONG_QUOTE = (
    "The only thing we have to fear is fear itself, nameless, unreasoning, unjustified terror which "
    "paralyzes needed efforts to convert retreat into advance."
)


def test_quote_card_is_2_45_plus_the_hold_when_the_quote_runs_to_three_lines_or_more():
    got = need(
        "quote-card",
        quote=LONG_QUOTE,
        attribution="FDR",
        source="Inaugural address, 1933",
    )
    assert got == pytest.approx(2.45 + max(1.2, 0.3 * words(LONG_QUOTE)))


def test_quote_card_with_a_one_line_quote_needs_half_a_second_less_than_the_hand_formula():
    """The hand formula assumes three lines: the block staggers its lines, so the line count (set by the block's own
    line breaking) decides the arrival. A short quote must not be refused a slot the block accepts."""
    got = need(
        "quote-card",
        quote="Fear itself.",
        attribution="FDR",
        source="Inaugural address, 1933",
    )
    assert got == pytest.approx(1.95 + 1.2)


def test_counter_is_4_2_plus_3_tenths_a_word_of_prefix_suffix_label_and_one():
    variables = {
        "from": 0,
        "to": 8198,
        "prefix": "",
        "suffix": "bars",
        "decimals": 0,
        "label": "of bullion counted in the mine",
        "thousandsSeparator": True,
    }
    assert need("counter", **variables) == pytest.approx(4.2 + 0.3 * (0 + 1 + 6 + 1))
    assert need("counter", **{**variables, "prefix": "$", "suffix": ""}) == pytest.approx(4.2 + 0.3 * (1 + 0 + 6 + 1))


def test_bar_grid_is_the_last_fill_the_pulse_the_reading_time_of_its_label_and_the_exit():
    # the cold open fills 0.4 + 3.0, then 0.7, then 1.2 + 0.3 a word of "found in the partial count" (5), then 0.5
    assert need("bar-grid", **COLD_OPEN) == pytest.approx(0.4 + 3.0 + 0.7 + 1.2 + 0.3 * 5 + 0.5)
    # the later shot ends with the 320-bar segment: 4.6 + 1.2, and 6 words in its label
    assert need("bar-grid", **FOR_SCALE) == pytest.approx(4.6 + 1.2 + 0.7 + 1.2 + 0.3 * 6 + 0.5)


ROWS = '[{"label":"Gold bars","value":"8,198","unit":"bars"},{"label":"Silver","value":"1,420","unit":"ingots"}]'
TOTAL = '{"label":"Items counted","value":"9,618"}'
BIG = '{"value":"98.6%","caption":"of the gold, per the final balance sheet"}'


def ledger(**overrides: str) -> dict[str, str]:
    return {
        "title": "Vault",
        "rows": ROWS,
        "total": TOTAL,
        "bigFigure": "",
        "rowTimes": "",
        **overrides,
    }


def test_ledger_with_row_times_is_the_last_row_plus_the_tail_the_reading_time_and_the_exit():
    got = need("ledger", **ledger(rowTimes="[0.5, 2.5]"))
    assert got == pytest.approx(2.5 + 1.9 + 1.2 + 0.3 * words("Items counted", "9,618") + 0.5)


def test_ledger_with_a_big_figure_reads_the_figure_and_its_caption_not_the_total_line():
    """The brief's hand formula reads the total line in both cases; the block reads the last thing it lands, which is the
    big figure and its caption, so that is what the preflight (the block's own function) uses."""
    got = need("ledger", **ledger(rowTimes="[0.5, 2.5]", bigFigure=BIG))
    assert got == pytest.approx(
        2.5 + 3.35 + 1.2 + 0.3 * words("98.6%", "of the gold, per the final balance sheet") + 0.5
    )


def test_ledger_with_evenly_spaced_rows_needs_a_micro_step_between_them():
    got = need("ledger", **ledger())
    assert got == pytest.approx(0.7 + 1 * 0.3 + 1.9 + 1.2 + 0.3 * words("Items counted", "9,618") + 0.5)


def test_ledger_row_times_that_cannot_be_written_are_the_blocks_own_error():
    (result,) = preflight.minimum_slots([("ledger", ledger(rowTimes="[0.5]"))], REG)

    assert result.seconds is None
    assert "rowTimes lists 1 times for 2 rows" in result.error


# -- the block and the preflight agree: the same script, so the same number -------------------------

DRIFT = (
    ("credit-line", {"text": "Bundesbank inventory, 1945. National Archives."}),
    ("date-stamp", {"text": "Late February to March 1945", "position": "bottom-left"}),
    (
        "lower-third",
        {
            "name": "Dwight Eisenhower",
            "role": ONE_LETTER_ROLE,
            "side": "left",
        },
    ),
    (
        "act-title",
        {
            "kicker": "Part 2",
            "title": "The salt mine at Marburg",
            "subtitle": "Where the Bundesbank hid its gold in the last weeks of the war",
        },
    ),
    (
        "quote-card",
        {
            "quote": LONG_QUOTE,
            "attribution": "FDR",
            "source": "Inaugural address, 1933",
        },
    ),
    (
        "quote-card",
        {
            "quote": "Fear itself.",
            "attribution": "FDR",
            "source": "Inaugural address, 1933",
        },
    ),
    (
        "counter",
        {
            "from": 0,
            "to": 8198,
            "prefix": "",
            "suffix": "bars",
            "decimals": 0,
            "label": "of bullion counted in the mine",
            "thousandsSeparator": True,
        },
    ),
    ("ledger", ledger()),
    ("ledger", ledger(rowTimes="[0.5, 2.5]", bigFigure=BIG)),
    ("bar-grid", COLD_OPEN),
    ("bar-grid", FOR_SCALE),
    # the bar blocks: the declared defaults are the sample (the page and the preflight both read them)
    ("split-bar", {}),
    ("split-bar", {"markers": "[]", "note": "Figures from the ledger itself"}),
    ("compare-bars", {}),
)

# date-stamp's page is reached through block_runner (block_harness finds the root by class); counter, ledger and bar-grid
# set CSS custom properties on their root, which neither stand-in page supports, so their slot check is run as the block
# calls it: countPlan, schedule and checkSlot, fed by the same planInput, scheduleInput and timingInput the page uses
LOGIC_CHECKS = {
    "counter": "const p = planInput(V, tok); p.duration = SLOT; countPlan(p);",
    "ledger": "const p = scheduleInput(readContent(V), tok); p.duration = SLOT; schedule(p);",
    "bar-grid": "const p = timingInput(tok); p.duration = SLOT; checkSlot(readConfig(V).segments, p);",
}


def page_error(name: str, variables: dict[str, Any], slot: float) -> str | None:
    """What the block's own script says about a slot: its error, or None when it accepts it."""
    merged = {**variables, "durationSeconds": slot}
    if name in LOGIC_CHECKS:
        program = (
            f"const V = {json.dumps(merged)}; const SLOT = {slot!r};"
            f"const props = {json.dumps(css_properties())};"
            "const tok = (name) => Number.parseFloat(props[name]);"
            f"{LOGIC_CHECKS[name]} emit(null);"
        )
        try:
            return node_run(BLOCKS / f"{name}.html", program)
        except LogicError as error:
            return str(error)
    if name == "date-stamp":
        return run_runner(BLOCKS / "date-stamp.html", merged, root="#stamp-root")["error"]
    return run_page(name, merged).error


@needs_node
@pytest.mark.parametrize(("name", "variables"), DRIFT)
def test_the_block_accepts_the_preflights_minimum_and_refuses_less(name, variables):
    minimum = need(name, **variables)

    accepted = page_error(name, variables, round(minimum + 0.001, 3))
    refused = page_error(name, variables, round(minimum - 0.01, 3))

    assert accepted is None, accepted
    assert refused and re.search(r"(shorter than the|needs at least) [\d.]+ s", refused)
    assert f"{minimum:.2f} s" in refused


# -- through plan: the error, the pass, the notes ---------------------------------------------------


@pytest.fixture
def assets(tmp_path):
    folder = tmp_path / "assets"
    for name in ("audio/voice.wav", "photos/mine.jpg", "clips/shot.mp4"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(name.encode())
    return folder


def shotlist(*shots: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "meta": {"title": "Preflight", "language": "en"},
        "audio": {"voice": {"path": "audio/voice.wav"}},
        "shots": list(shots),
    }


def shot(shot_id: str, layers: list[dict[str, Any]], start: float = 0, duration: float = 12):
    return {"id": shot_id, "start": start, "duration": duration, "layers": layers}


def credit(text: str = "US Army Signal Corps, NARA 111-SC-204515", **layer: Any):
    return {
        "component": "credit-line",
        "at": 0.2,
        "variables": {"text": text, "align": "left"},
        **layer,
    }


def photo(**layer: Any):
    variables = {
        "image": "photos/mine.jpg",
        "from": "[0, 0, 1, 1]",
        "to": "[0.1, 0.1, 0.8, 0.8]",
        "grade": "{}",
        "scrim": "none",
    }
    return {"component": "photo-shot", "at": 0, "variables": variables, **layer}


def clip(**layer: Any):
    variables = {
        "video": "clips/shot.mp4",
        "trimStart": 0,
        "grade": "{}",
        "scrim": "none",
        "hold": "freeze",
    }
    return {"component": "video-shot", "at": 0, "variables": variables, **layer}


def card(**layer: Any):
    variables = {
        "quote": "Fear itself.",
        "attribution": "FDR",
        "source": "Inaugural address, 1933",
    }
    return {
        "component": "quote-card",
        "at": 0,
        **layer,
        "variables": {**variables, **layer.pop("variables", {})},
    }


def route(**layer: Any):
    variables = {
        "region": "germany",
        "pins": "[]",
        "route": "[]",
        "yearLabel": "1945",
        "caption": "Route indicative",
        "cameraFrom": '{"lon":11.75,"lat":51.7,"span":9.6}',
        "cameraTo": '{"lon":11.75,"lat":51.7,"span":9.6}',
        "routeStyle": "rail",
        "borders": "none",
    }
    return {
        "component": "map-route",
        "at": 0,
        **layer,
        "variables": {**variables, **layer.pop("variables", {})},
    }


def run_plan(raw, assets, voice=120.0):
    return plan(parse_shotlist(raw), [], voice, assets_dir=assets)


def refused(raw, assets) -> list[str]:
    with pytest.raises(ShotListError) as error:
        run_plan(raw, assets)
    return error.value.problems


def test_a_credit_line_too_short_for_its_words_is_refused_and_the_minimum_passes(
    assets,
):
    text = "Bundesbank inventory, 1945. National Archives."  # 5 words: 3.9 s
    for slot in (3.5, 3.8):
        problems = refused(shotlist(shot("s", [credit(text, duration=slot)])), assets)
        assert any("shot 's', layer 1 (credit-line)" in p and "3.90 s" in p for p in problems), problems
    assert run_plan(shotlist(shot("s", [credit(text, duration=3.9)])), assets).layers[0].slot == 3.9


def test_a_slot_under_the_static_minimum_is_one_error_not_two(assets):
    problems = refused(shotlist(shot("s", [credit(duration=2.0)])), assets)

    assert len([p for p in problems if "layer 1 (credit-line)" in p]) == 1


def test_a_lower_third_with_a_long_role_needs_more_than_its_static_minimum(assets):
    layer = {
        "component": "lower-third",
        "at": 0,
        "duration": 5.8,
        "variables": {
            "name": "Dwight Eisenhower",
            "role": ONE_LETTER_ROLE,
            "side": "left",
        },
    }

    problems = refused(shotlist(shot("s", [layer])), assets)

    assert any("layer 1 (lower-third)" in p and "shorter than" in p for p in problems), problems


def test_a_block_that_would_refuse_its_variables_is_an_error_whatever_the_slot(assets):
    layer = {
        "component": "credit-line",
        "at": 0,
        "variables": {"text": "", "align": "left"},
    }

    problems = refused(shotlist(shot("s", [layer])), assets)

    assert any("layer 1 (credit-line)" in p and "text variable is missing or empty" in p for p in problems)


def test_a_layer_the_composer_already_refused_for_its_variables_is_not_refused_twice(
    assets,
):
    layer = {"component": "credit-line", "at": 0, "variables": {"align": "left"}}

    problems = refused(shotlist(shot("s", [layer])), assets)

    assert len(problems) == 1 and "variable 'text'" in problems[0]


def test_the_page_sees_the_declared_defaults_under_what_the_layer_sets(assets):
    """A sample layer leaves variables out and the page then reads the block's defaults, so the preflight must too."""
    layer = {"component": "credit-line", "at": 0, "sample": True, "variables": {}}

    # without the merge the block would say its text variable is missing, and the plan would refuse the layer
    assert run_plan(shotlist(shot("s", [layer])), assets).layers[0].slot == 12


def test_a_map_is_noted_as_not_pre_checked_and_the_note_says_which_shots(assets):
    result = run_plan(
        shotlist(
            shot("m1", [route()], duration=8),
            shot("m2", [route()], start=8, duration=8),
        ),
        assets,
    )

    notes = [w for w in result.warnings if "not pre-checked" in w]
    assert len(notes) == 1
    assert "map-route" in notes[0] and "'m1'" in notes[0] and "'m2'" in notes[0]
    assert "camera" in notes[0]


def test_a_pre_checked_block_gets_no_note(assets):
    result = run_plan(shotlist(shot("s", [credit()])), assets)

    assert not [w for w in result.warnings if "not pre-checked" in w]


def test_without_node_the_minimums_are_a_warning_not_an_error_and_not_silence(assets, monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)

    result = run_plan(shotlist(shot("s", [credit(duration=2.5)])), assets)

    assert any("credit-line" in w and "node" in w and "not pre-checked" in w for w in result.warnings)


def bars_layer(component: str, **layer: Any) -> dict[str, Any]:
    """A layer of a bar block with the variables of its declared defaults (the composer wants every one written out)."""
    variables = {v["id"]: v["default"] for v in REG.items[component].variables if v["id"] != "durationSeconds"}
    return {
        "component": component,
        "at": 0,
        **layer,
        "variables": variables | layer.pop("variables", {}),
    }


def test_a_split_bar_too_short_for_its_last_bracket_is_refused_and_the_minimum_passes(
    assets,
):
    """The sample's bracket arrives at 5 s, takes 0.7 s to come in, is read for 1.2 + 0.3 for 9 words and exits in 0.5."""
    for slot in (7.5, 10.09):
        problems = refused(shotlist(shot("s", [bars_layer("split-bar", duration=slot)])), assets)
        assert any("layer 1 (split-bar)" in p and "10.10 s" in p for p in problems), problems
    result = run_plan(shotlist(shot("s", [bars_layer("split-bar", duration=10.1)])), assets)
    assert result.layers[0].slot == 10.1


def test_a_compare_bars_layer_too_short_for_its_second_bar_is_refused_and_the_minimum_passes(
    assets,
):
    problems = refused(shotlist(shot("s", [bars_layer("compare-bars", duration=8.0)])), assets)

    assert any("layer 1 (compare-bars)" in p and "8.50 s" in p for p in problems), problems
    result = run_plan(shotlist(shot("s", [bars_layer("compare-bars", duration=8.5)])), assets)
    assert result.layers[0].slot == 8.5


def test_a_split_bar_whose_label_has_no_free_place_is_an_error_at_plan_time(assets):
    crowded = json.dumps(
        [
            {
                "label": f"label {i}",
                "display": "about",
                "value": 20 if i else 30,
                "tone": "gold",
                "at": i,
                "seconds": 1,
            }
            for i in range(4)
        ]
    )
    layer = bars_layer("split-bar", variables={"parts": crowded, "markers": "[]"})

    problems = refused(shotlist(shot("s", [layer])), assets)

    assert any("layer 1 (split-bar)" in p and "no free place" in p for p in problems), problems


# -- layers hidden behind a layer that covers the frame ---------------------------------------------


def hidden_warnings(result) -> list[str]:
    return [w for w in result.warnings if "hidden" in w]


def test_the_flags_follow_the_blocks_markup():
    """A block covers the frame when it paints an opaque ground the size of the frame; the others never do."""
    expected = {
        "act-title": ".at-ground",
        "quote-card": ".qc-ground",
        "photo-shot": "#photo-root",
        "video-shot": "#video-root",
        "map-route": ".stage",
    }
    flagged = {i.name for i in registry.list_items(REG) if i.covers_frame}
    assert flagged == set(expected)
    for name, selector in expected.items():
        rule = rule_of(name, selector)
        assert re.search(r"background:\s*var\(--(color-night|sea)\)", rule), (
            name,
            rule,
        )


def test_a_layer_under_a_full_frame_layer_is_hidden_for_the_overlap(assets):
    raw = shotlist(shot("s", [route(), card(at=4)], duration=10))

    result = run_plan(raw, assets)

    (warning,) = hidden_warnings(result)
    assert "shot 's', layer 1 (map-route)" in warning
    assert "6.00 s" in warning and "layer 2 (quote-card)" in warning


def test_a_layer_above_the_covering_one_is_not_hidden(assets):
    raw = shotlist(shot("s", [card(at=0), credit(text="Source: the archive", at=0.5, duration=4)]))

    assert hidden_warnings(run_plan(raw, assets)) == []


def test_equal_tracks_stack_in_list_order(assets):
    below = shotlist(shot("s", [route(track=2), card(track=2)]))
    above = shotlist(shot("s", [card(track=2), route(track=2)]))

    assert len(hidden_warnings(run_plan(below, assets))) == 1
    (warning,) = hidden_warnings(run_plan(above, assets))
    assert "layer 1 (quote-card)" in warning


def test_the_track_decides_not_the_list_position(assets):
    # the map is first in the list but on the higher track: it is in front, the card is the hidden one
    raw = shotlist(shot("s", [route(track=5), card(track=0)]))

    (warning,) = hidden_warnings(run_plan(raw, assets))
    assert "layer 2 (quote-card)" in warning and "layer 1 (map-route)" in warning


def test_a_layer_hidden_for_less_than_three_tenths_of_a_second_is_ignored(assets):
    # the card comes in 0.25 s before the end of the lower layer
    raw = shotlist(shot("s", [route(duration=5.0), card(at=4.75)], duration=10))

    assert hidden_warnings(run_plan(raw, assets)) == []


def test_two_covering_layers_hide_the_union_of_their_windows_once(assets):
    raw = shotlist(
        shot(
            "s",
            [route(), card(at=2, duration=4, track=3), card(at=4, duration=4, track=4)],
            duration=12,
        )
    )

    (warning,) = [w for w in hidden_warnings(run_plan(raw, assets)) if "layer 1 (map-route)" in w]
    assert "6.00 s" in warning  # 2 to 8, not 4 + 4


def test_a_layer_hidden_for_its_whole_slot_says_it_never_shows(assets):
    raw = shotlist(
        shot(
            "s",
            [credit(text="Route indicative", at=0.4, duration=5), route(track=5)],
            duration=8,
        )
    )

    (warning,) = hidden_warnings(run_plan(raw, assets))
    assert "layer 1 (credit-line)" in warning and "never shows" in warning
    assert "layer 2 (map-route)" in warning


def test_a_map_above_its_credit_line_is_the_second_real_failure_of_the_gold_test(
    assets,
):
    """swiss-map: a map-route on a higher track than its credit-line hid the 'Route indicative' credit."""
    broken = shotlist(
        shot(
            "swiss-map",
            [
                route(track=1),
                credit(text="Route indicative", at=0.4, duration=5.0, track=0),
            ],
            duration=13,
        )
    )
    fixed = shotlist(
        shot(
            "swiss-map",
            [
                route(track=0),
                credit(text="Route indicative", at=0.4, duration=5.0, track=1),
            ],
            duration=13,
        )
    )

    (warning,) = hidden_warnings(run_plan(broken, assets))
    assert "shot 'swiss-map', layer 2 (credit-line)" in warning
    assert hidden_warnings(run_plan(fixed, assets)) == []


def test_a_photo_credited_by_a_line_hidden_under_a_later_card_is_the_first_real_failure(
    assets,
):
    """weber, broken: a photo, its credit line and a quote card that starts 0.4 s in hid the photo almost at once."""
    broken = shotlist(
        shot(
            "weber",
            [photo(), credit(at=0.3, duration=5.5, track=1), card(at=0.4, track=7)],
            duration=10,
        )
    )

    problems = refused(broken, assets)

    assert any("shot 'weber', layer 1 (photo-shot)" in p and "credit line" in p and "hidden" in p for p in problems), (
        problems
    )


def test_the_fixed_weber_shot_passes_with_warnings_for_what_the_card_covers(assets):
    """The card starts 3.5 s in: the credit line was on screen with the photo for 3.3 s, so the photo is credited;
    the photo, the credit and the lower third are hidden behind the card for the rest and each is a warning."""
    lower = {
        "component": "lower-third",
        "at": 0.8,
        "duration": 5.8,
        "variables": {"name": "Carl Weber", "role": "Swiss banker", "side": "left"},
    }
    fixed = shotlist(
        shot(
            "weber",
            [
                photo(),
                credit(at=0.2, duration=5.5, track=1),
                lower | {"track": 2},
                card(at=3.5, track=7),
            ],
            duration=10,
        )
    )

    result = run_plan(fixed, assets)

    blocks = " ".join(hidden_warnings(result))
    for expected in ("photo-shot", "credit-line", "lower-third"):
        assert expected in blocks


def test_a_credit_line_under_its_own_photo_is_an_error_because_it_is_never_seen(assets):
    raw = shotlist(shot("s", [credit(track=0), photo(track=1)]))

    problems = refused(raw, assets)

    assert any("layer 2 (photo-shot)" in p and "credit line" in p and "hidden" in p for p in problems), problems


def test_a_clip_hides_what_is_under_it_like_a_photo_and_needs_its_credit_in_sight(
    assets,
):
    under = refused(shotlist(shot("s", [credit(track=0), clip(track=1)])), assets)
    above = run_plan(shotlist(shot("s", [clip(track=0), credit(track=1)])), assets)
    behind_a_card = refused(
        shotlist(
            shot(
                "weber",
                [clip(), credit(at=0.3, duration=5.5, track=1), card(at=0.4, track=7)],
                duration=10,
            )
        ),
        assets,
    )

    assert any("layer 2 (video-shot)" in p and "credit line" in p and "hidden" in p for p in under), under
    assert hidden_warnings(above) == []
    assert any("shot 'weber', layer 1 (video-shot)" in p and "credit line" in p for p in behind_a_card), behind_a_card


def test_the_flags_of_a_block_that_shows_a_picture_are_set_in_the_registry():
    """photo-shot and video-shot cover the frame and need a credit line in sight; the other blocks need none."""
    credited = {i.name for i in registry.list_items(REG) if i.needs_credit}

    assert credited == {"photo-shot", "video-shot"}
    assert all(registry.get(name, REG).covers_frame for name in credited)


def test_hiding_with_no_photo_involved_is_never_an_error(assets):
    raw = shotlist(shot("s", [route(track=0), card(track=4)]))

    result = run_plan(raw, assets)

    assert len(hidden_warnings(result)) == 1


def test_shots_do_not_hide_each_other(assets):
    raw = shotlist(
        shot("a", [card()], duration=6),
        shot("b", [photo(), credit()], start=6, duration=6),
    )

    assert hidden_warnings(run_plan(raw, assets)) == []


def test_the_registry_flag_must_be_true_or_false_and_only_a_block_has_it():
    raw = json.loads((tokens.MOTION_DIR / "registry.json").read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(raw))
    next(i for i in bad["items"] if i["name"] == "act-title")["coversFrame"] = "yes"
    with pytest.raises(registry.RegistryError, match="coversFrame: must be true or false"):
        registry.parse_registry(bad)

    bad = json.loads(json.dumps(raw))
    next(i for i in bad["items"] if i["name"] == "map-kit")["coversFrame"] = True
    with pytest.raises(registry.RegistryError, match="only a block has coversFrame"):
        registry.parse_registry(bad)


def test_the_credit_flag_must_be_true_or_false_and_only_a_block_has_it():
    raw = json.loads((tokens.MOTION_DIR / "registry.json").read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(raw))
    next(i for i in bad["items"] if i["name"] == "photo-shot")["needsCredit"] = "yes"
    with pytest.raises(registry.RegistryError, match="needsCredit: must be true or false"):
        registry.parse_registry(bad)

    bad = json.loads(json.dumps(raw))
    next(i for i in bad["items"] if i["name"] == "map-kit")["needsCredit"] = True
    with pytest.raises(registry.RegistryError, match="only a block has needsCredit"):
        registry.parse_registry(bad)
