"""The ``photo-shot`` block: the contract, and the camera clamp, which is played against the real GSAP in node."""

from __future__ import annotations

import json
import random
import re
from functools import partial

import pytest

from hyperframes_mcp.motion import lint, registry, tokens
from tests.motion.block_runner import (
    GSAP,
    code_of,
    declared_variables,
    default_variables,
    needs_node,
    read_block,
    run_block,
    run_node,
)

BLOCK = tokens.MOTION_DIR / "blocks" / "photo-shot.html"
html = partial(read_block, BLOCK)
declared = partial(declared_variables, BLOCK)
defaults = partial(default_variables, BLOCK)


# -- the contract, as far as a static test can see it --------------------------------------------


def test_it_passes_the_linter():
    assert lint.lint_file(BLOCK) == []


def test_it_registers_one_paused_timeline_with_one_fromto_that_fills_the_slot():
    text = code_of(html())

    assert 'data-composition-id="photo-shot"' in text
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert 'window.__timelines["photo-shot"] = tl;' in text
    assert text.count(".fromTo(") == 1
    assert not re.search(r"\.(?:to|from)\(|gsap\.set\(|tl\.set\(", text)
    # the single tween starts at 0 and lasts exactly the slot
    assert re.search(r"duration: duration, ease: \"power1\.inOut\" \},\s*0,", text)


def test_the_move_tweens_only_scale_x_and_y_and_never_the_graded_image():
    text = code_of(html())
    tween = re.search(r"tl\.fromTo\((.*?)\n        \);", text, re.DOTALL).group(1)

    assert tween.strip().startswith("cameraEl")
    assert set(re.findall(r"\b(scale|x|y|opacity|rotation)\s*:", tween)) == {
        "scale",
        "x",
        "y",
    }
    # nothing else in the file touches opacity either: a graded image faded by opacity starts from black
    assert not re.search(r"\bopacity\b", re.search(r"<script>.*", text, re.DOTALL).group())


def test_it_uses_the_move_ease_with_a_matching_written_out_curve():
    """The clamp samples the ease it plays: power1.inOut is 2t^2 up to the middle and 1 - 2(1-t)^2 after."""
    text = code_of(html())

    assert 'ease: "power1.inOut"' in text
    assert "t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t)" in text
    assert "power1.inOut" in lint._EXTRA_EASES


def test_the_limits_and_the_finish_come_from_the_tokens_css():
    text = code_of(html())
    css = tokens.css()
    t = tokens.default_tokens()

    for name in (
        "--camera-max-pan-px-per-s",
        "--camera-max-zoom-per-s",
        "--dur-cinematic-min",
    ):
        assert f'token("{name}")' in text
    # every camera and finish number of the tokens is a custom property of tokens.css
    for section, values in (("camera", t.camera), ("finish", t.finish)):
        for key, value in values.items():
            assert f"--{section}-{key.replace('_', '-')}: {value:g};" in css


def test_the_finish_is_always_applied_through_data_color_grading():
    text = code_of(html())

    assert 'photoEl.setAttribute(\n          "data-color-grading"' in text
    assert 'vignette: Number.parseFloat(token("--finish-vignette"))' in text
    assert 'grain: Number.parseFloat(token("--finish-grain"))' in text
    assert "adjust: adjust" in text


def test_the_scrim_is_a_token_gradient_with_three_states():
    text = html()
    scrim = next(v for v in declared() if v["id"] == "scrim")

    assert [o["value"] for o in scrim["options"]] == ["none", "left", "bottom"]
    assert scrim["default"] == "bottom"
    assert 'data-scrim="bottom"' in text and 'data-scrim="left"' in text
    assert "var(--color-scrim)" in text
    assert not re.search(r"text-shadow|box-shadow|drop-shadow", text)


def test_the_variables_are_declared_in_order_with_a_six_second_default():
    ids = [v["id"] for v in declared()]

    assert ids == ["durationSeconds", "image", "from", "to", "grade", "scrim"]
    assert defaults()["durationSeconds"] == 6


def test_a_missing_or_wrong_variable_throws_instead_of_drawing_a_wrong_frame():
    text = code_of(html())

    for message in (
        "the image variable is missing or empty",
        "must be [x, y, width, height]",
        "must lie inside the image",
        "must have the same aspect",
        "grade must be a JSON object",
        "is not a known adjustment in range",
        "scrim must be none, left or bottom",
        "is shorter than the",
    ):
        assert message in text, message
    assert text.count("throw new Error") >= 9
    # the slot's floor is the cinematic minimum of the tokens, and the declared minimum says the same
    assert declared()[0]["min"] == tokens.default_tokens().duration["cinematic_min"]


def test_the_default_crops_and_grade_are_valid_and_keep_one_aspect():
    d = defaults()
    start, end = json.loads(d["from"]), json.loads(d["to"])
    grade = json.loads(d["grade"])

    assert start[3] / start[2] == pytest.approx(end[3] / end[2], rel=0.01)
    for x, y, w, h in (start, end):
        assert x >= 0 and y >= 0 and x + w <= 1 and y + h <= 1
    assert set(grade) <= {"exposure", "contrast", "temperature"}


def test_no_photograph_is_stored_in_the_library_and_the_default_image_is_a_sample_projects():
    suffixes = {".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".heic"}
    photos = [p.name for p in tokens.MOTION_DIR.rglob("*") if p.suffix.lower() in suffixes]
    default_image = defaults()["image"]
    registered = {e.path for item in registry.list_items() for e in item.files}
    notes = registry.get("photo-shot").notes

    assert photos == []
    assert not (tokens.MOTION_DIR / default_image).exists()
    assert default_image not in registered
    assert "sample project supplies" in notes and default_image in notes


# -- the camera clamp, against the real GSAP -----------------------------------------------------

HARNESS = r"""
const fs = require("fs");
const gsap = require(%(gsap)s).gsap;
%(math)s
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const W = 1920, H = 1080, N = 2400;
const ease = gsap.parseEase("power1.inOut");
const out = input.cases.map((c) => {
  const plan = planMove(c.from, c.to, c.seconds, W, H, input.maxPan, input.maxZoom);
  const a = plan.start, b = plan.end;
  const o = { scale: 1, x: 0, y: 0 };
  const tl = gsap.timeline({ paused: true });
  tl.fromTo(
    o,
    { scale: a.scale, x: a.x, y: a.y },
    { scale: b.scale, x: b.x, y: b.y, duration: c.seconds, ease: "power1.inOut" },
    0,
  );
  let prev = null, maxPan = 0, maxZoom = 0, outside = 0, tweenDiff = 0;
  let first = null, last = null;
  for (let i = 0; i <= N; i++) {
    // the values GSAP's own ease gives a straight-line tween of scale, x and y, and the tween itself to check them
    const p = ease(i / N);
    const s = a.scale + p * (b.scale - a.scale), x = a.x + p * (b.x - a.x), y = a.y + p * (b.y - a.y);
    tl.time((c.seconds * i) / N);
    tweenDiff = Math.max(tweenDiff, Math.abs(o.scale - s), Math.abs(o.x - x), Math.abs(o.y - y));
    const cx = (W / 2 - x) / s, cy = (H / 2 - y) / s;
    const crop = [-x / (s * plan.lw), -y / (s * plan.lh), W / (s * plan.lw), H / (s * plan.lh)];
    if (crop[0] < -1e-9 || crop[1] < -1e-9 || crop[0] + crop[2] > 1 + 1e-9 || crop[1] + crop[3] > 1 + 1e-9) outside++;
    if (i === 0) first = crop;
    if (i === N) last = crop;
    if (prev) {
      const dt = c.seconds / N;
      maxPan = Math.max(maxPan, (Math.hypot(cx - prev.cx, cy - prev.cy) * (s + prev.s)) / 2 / dt);
      maxZoom = Math.max(maxZoom, Math.abs(Math.log(s / prev.s)) / dt);
    }
    prev = { cx, cy, s };
  }
  return { factor: plan.factor, over: plan.over, lw: plan.lw, lh: plan.lh, maxPan, maxZoom, outside, first, last, tweenDiff };
});
console.log(JSON.stringify(out));
"""


def play(cases: list[dict]) -> list[dict]:
    """Run planMove from the block and play each result in the real GSAP; the speeds and crops it measured."""
    text = html()
    math = text[text.index("// <move-math>") : text.index("// </move-math>")]
    script = HARNESS % {"gsap": json.dumps(str(GSAP)), "math": math}
    t = tokens.default_tokens()
    payload = {
        "cases": cases,
        "maxPan": t.camera["max_pan_px_per_s"],
        "maxZoom": t.camera["max_zoom_per_s"],
    }
    return json.loads(run_node(script, payload))


def box(rng: random.Random, aspect: float, width: float) -> list[float]:
    """A random crop of the given width fraction that lies inside the image; ``aspect`` is height / width."""
    height = width * aspect
    return [
        round(rng.uniform(0, 1 - width), 4),
        round(rng.uniform(0, 1 - height), 4),
        width,
        round(height, 4),
    ]


def random_cases(count: int) -> list[dict]:
    rng = random.Random(20261005)
    cases = []
    for _ in range(count):
        aspect = rng.choice([1.0, 0.7126, 0.5625, 1.2])
        widest = min(1.0, 1 / aspect)
        cases.append(
            {
                "from": box(rng, aspect, rng.uniform(0.3, widest)),
                "to": box(rng, aspect, rng.uniform(0.3, widest)),
                "seconds": round(rng.uniform(1.2, 12), 2),
            }
        )
    return cases


@needs_node
def test_the_default_move_is_not_clamped_and_shows_exactly_the_two_crops():
    d = defaults()
    case = {
        "from": json.loads(d["from"]),
        "to": json.loads(d["to"]),
        "seconds": d["durationSeconds"],
    }
    [result] = play([case])

    assert result["factor"] == 1
    assert result["first"] == pytest.approx(case["from"], abs=1e-9)
    assert result["last"] == pytest.approx(case["to"], abs=1e-9)
    assert result["outside"] == 0


@needs_node
def test_a_violent_move_is_pulled_back_along_its_own_direction_and_stays_slow():
    t = tokens.default_tokens()
    case = {"from": [0, 0, 1, 1], "to": [0.7, 0.7, 0.3, 0.3], "seconds": 2}
    [result] = play([case])

    assert 0 < result["factor"] < 1
    assert result["first"] == pytest.approx(case["from"], abs=1e-9)
    # the tamed end is the start moved a fraction of the way toward the asked end, centre and (log) width
    fx = result["factor"]
    w = case["from"][2] * (case["to"][2] / case["from"][2]) ** fx
    cx = 0.5 + fx * (0.85 - 0.5)
    assert result["last"] == pytest.approx([cx - w / 2, cx - w / 2, w, w], abs=1e-6)
    assert result["maxPan"] <= t.camera["max_pan_px_per_s"]
    assert result["maxZoom"] <= t.camera["max_zoom_per_s"]
    # tight: the clamp does not throw the move away (one of the two limits is nearly reached)
    assert (
        max(
            result["maxPan"] / t.camera["max_pan_px_per_s"],
            result["maxZoom"] / t.camera["max_zoom_per_s"],
        )
        > 0.97
    )


@needs_node
def test_no_crop_pair_ever_pans_or_zooms_faster_than_the_tokens_allow():
    t = tokens.default_tokens()
    cases = random_cases(120)
    results = play(cases)

    assert sum(r["factor"] < 1 for r in results) > 30  # the cases do exercise the clamp
    for case, r in zip(cases, results, strict=True):
        assert r["maxPan"] <= t.camera["max_pan_px_per_s"], case
        assert r["maxZoom"] <= t.camera["max_zoom_per_s"], case
        assert r["outside"] == 0, case  # the camera never shows beyond the photograph
        assert r["tweenDiff"] < 1e-4, case  # GSAP's tween is the straight line the clamp played
        assert r["first"] == pytest.approx(case["from"], abs=1e-9), case


@needs_node
def test_a_still_shot_stays_still_and_a_zoom_out_is_not_oversampled():
    still = {"from": [0.1, 0.1, 0.6, 0.6], "to": [0.1, 0.1, 0.6, 0.6], "seconds": 5}
    out = {"from": [0.2, 0.2, 0.5, 0.5], "to": [0, 0, 1, 1], "seconds": 20}
    still_result, out_result = play([still, out])

    assert still_result["maxPan"] == 0 and still_result["maxZoom"] == 0
    assert still_result["factor"] == 1
    assert out_result["over"] == 1  # the layer is already sized for the widest view of the move


@needs_node
def test_the_canvas_is_oversampled_for_the_zoom_in_but_never_beyond_three_times():
    zoom_in = {"from": [0, 0, 1, 1], "to": [0.2, 0.2, 0.6, 0.6], "seconds": 30}
    deep = {"from": [0, 0, 1, 1], "to": [0.4, 0.4, 0.1, 0.1], "seconds": 60}
    a, b = play([zoom_in, deep])

    # the layer is as many times the frame as the camera zooms in (1 / 0.6 here), the canvas capped at three
    assert a["factor"] == 1
    assert a["over"] == pytest.approx(1 / 0.6)
    assert a["lw"] == pytest.approx(1920 * a["over"])
    assert b["over"] == 3 and b["lw"] == pytest.approx(1920 * 3)


@needs_node
def test_a_zoom_out_from_a_tight_crop_never_asks_for_a_canvas_beyond_the_layer_limit():
    """The layer is the whole photograph at the sharpest crop's scale: 19200 px wide for a crop a tenth wide."""
    case = {"from": [0.4, 0.4, 0.1, 0.1], "to": [0, 0, 1, 1], "seconds": 60}
    [result] = play([case])
    t = tokens.default_tokens()

    assert result["lw"] == pytest.approx(8192)  # the limit, not 1920 / 0.1
    assert result["over"] < 1  # the canvas is shown a little enlarged instead
    assert result["first"] == pytest.approx(case["from"], abs=1e-9)
    assert result["outside"] == 0
    assert result["maxPan"] <= t.camera["max_pan_px_per_s"]
    assert result["maxZoom"] <= t.camera["max_zoom_per_s"]


# -- the whole script, run in node against the real GSAP -----------------------------------------


def run(variables=None, after="return null;", **options):
    return run_block(BLOCK, variables, after=after, root="#photo-root", **options)


GRADED = r"""
const photo = els[".photo"], camera = els[".camera"], scrim = els[".scrim"];
return {
  duration: tl.duration(),
  grading: JSON.parse(photo.attrs["data-color-grading"]),
  src: photo.attrs["src"], id: photo.id, scrim: scrim.attrs["data-scrim"],
  layer: [camera.style.width, camera.style.height],
  tweens: tl.getChildren(false, true, false).length,
};
"""


@needs_node
def test_the_timeline_fills_the_slot_exactly_at_any_length():
    for seconds in (1.2, 6, 30, 60):
        out = run({"durationSeconds": seconds}, "return tl.duration();")

        assert out["error"] is None, out
        assert out["result"] == pytest.approx(seconds, abs=1e-9), seconds


@needs_node
def test_the_defaults_alone_build_a_good_shot_with_the_library_finish_applied():
    t = tokens.default_tokens()
    out = run(None, GRADED)

    assert out["error"] is None, out
    shot = out["result"]
    assert shot["tweens"] == 1 and shot["duration"] == 6
    assert shot["src"] == "assets/img/sample-photo.jpg"
    assert shot["scrim"] == "bottom"
    grading = shot["grading"]
    assert grading["details"]["vignette"] == t.finish["vignette"]
    assert grading["details"]["grain"] == t.finish["grain"]
    assert grading["adjust"] == json.loads(defaults()["grade"])
    assert grading["preset"] == "neutral"


@needs_node
def test_the_variables_of_a_mount_reach_the_picture_as_json_text_or_as_values():
    as_text = run(
        {
            "image": "assets/img/a04.jpg",
            "from": "[0, 0.14, 1, 0.7154]",
            "to": "[0.2, 0.2, 0.5, 0.3577]",
            "grade": '{"exposure": 0.2, "temperature": 0.1}',
            "scrim": "left",
        },
        GRADED,
    )
    as_values = run(
        {
            "image": "assets/img/a04.jpg",
            "from": [0, 0.14, 1, 0.7154],
            "to": [0.2, 0.2, 0.5, 0.3577],
            "grade": {"exposure": 0.2, "temperature": 0.1},
            "scrim": "left",
        },
        GRADED,
    )

    assert as_text["error"] is None and as_text == as_values
    shot = as_text["result"]
    assert shot["src"] == "assets/img/a04.jpg" and shot["scrim"] == "left"
    assert shot["grading"]["adjust"] == {"exposure": 0.2, "temperature": 0.1}


@needs_node
def test_the_grain_seed_id_comes_from_the_path_so_two_renders_agree():
    """The shader seeds its grain from the image id, or from the URL (with the render's port) when there is none."""
    a = run({"image": "assets/img/a04.jpg"}, GRADED)["result"]["id"]
    b = run({"image": "assets/img/a04.jpg"}, GRADED)["result"]["id"]
    c = run({"image": "assets/img/a05.jpg"}, GRADED)["result"]["id"]

    assert a == b == "photo-assets-img-a04-jpg"
    assert c != a
    assert re.fullmatch(r"photo-[A-Za-z0-9-]+", a)


@needs_node
def test_a_bad_variable_fails_the_render_with_a_message_that_names_it():
    bad = {
        "image": ({"image": ""}, "image variable is missing or empty"),
        "json": ({"from": "[0, 0, 1"}, "from is not valid JSON"),
        "shape": ({"to": "[0, 0, 1]"}, "to must be [x, y, width, height]"),
        "outside": ({"to": "[0.5, 0.5, 0.8, 0.8]"}, "to must lie inside the image"),
        "aspect": ({"to": "[0, 0, 0.5, 0.9]"}, "from and to must have the same aspect"),
        "grade type": ({"grade": "[1]"}, "grade must be a JSON object"),
        "grade key": (
            {"grade": '{"gamma": 0.1}'},
            "grade.gamma is not a known adjustment",
        ),
        "grade range": (
            {"grade": '{"contrast": 3}'},
            "grade.contrast is not a known adjustment in range",
        ),
        "scrim": ({"scrim": "top"}, "scrim must be none, left or bottom"),
        "short": ({"durationSeconds": 1.1}, "is shorter than the 1.2 s"),
    }
    for name, (variables, message) in bad.items():
        out = run(variables)

        assert out["error"] and message in out["error"], (name, out)


@needs_node
def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    """A mounted block's <html> is not in the page: there is no declaration to read, only getVariables()."""
    out = run(None, "return tl.duration();", mounted=True)
    moved = run({"durationSeconds": 9, "scrim": "left"}, GRADED, mounted=True)

    assert out["error"] is None and out["result"] == 6
    assert moved["error"] is None
    assert moved["result"]["duration"] == 9 and moved["result"]["scrim"] == "left"


@needs_node
def test_a_tokens_css_that_did_not_load_fails_instead_of_guessing_a_limit():
    # the runner answers every custom property from tokens.css; one with an empty value is "not loaded"
    out = run_block(
        BLOCK,
        None,
        after="return null;",
        root="#photo-root",
        css={"--camera-max-pan-px-per-s": ""},
    )

    assert out["error"] and "tokens.css is not loaded: --camera-max-pan-px-per-s" in out["error"]
