"""``hyperframes_mcp.motion.grade``: the per-photo levelling that puts the median luma inside the target band."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from hyperframes_mcp.motion import grade


def save(tmp_path, name: str, luma: np.ndarray) -> str:
    """Write ``luma`` (floats 0..1, any shape) as an image and return its path."""
    data = (np.clip(luma, 0, 1) * 255 + 0.5).astype(np.uint8)
    path = tmp_path / name
    Image.fromarray(data).save(path)
    return str(path)


def ramp(low: float, high: float, size: int = 90) -> np.ndarray:
    """A horizontal ramp from ``low`` to ``high``: the median is their midpoint, the range is known."""
    return np.tile(np.linspace(low, high, size, dtype=np.float32), (size, 1))


def after_grade(luma: float, adjust: dict) -> float:
    """A luma value after the shader's primary grade (exposure, then contrast around 0.5).

    This is the arithmetic of the HyperFrames runtime shader (applyPrimaryGrade, unchanged since 0.8.101), written out:
    ``color *= 2^exposure`` and ``(color - 0.5) * (1 + contrast) + 0.5``.
    """
    x = luma * 2.0 ** adjust["exposure"]
    return (x - 0.5) * (1.0 + adjust["contrast"]) + 0.5


def test_median_luma_of_a_flat_image_is_its_grey(tmp_path):
    path = save(tmp_path, "flat.png", np.full((60, 60), 0.4))

    assert grade.median_luma(path) == pytest.approx(0.4, abs=0.01)


def test_median_luma_uses_the_rec709_weights_on_a_colour_image(tmp_path):
    green = np.zeros((30, 30, 3), np.uint8)
    green[..., 1] = 255
    path = tmp_path / "green.png"
    Image.fromarray(green).save(path)

    assert grade.median_luma(path) == pytest.approx(0.7152, abs=0.01)


def test_median_luma_reads_an_image_with_an_alpha_profile(tmp_path):
    rgba = np.full((20, 20, 4), 255, np.uint8)
    rgba[..., :3] = 51
    path = tmp_path / "alpha.png"
    Image.fromarray(rgba).save(path)

    assert grade.median_luma(path) == pytest.approx(0.2, abs=0.01)


def test_a_dark_photo_is_lifted_into_the_band(tmp_path):
    path = save(tmp_path, "dark.png", ramp(0.0, 0.2))
    adjust = grade.grade_for(path)

    assert grade.median_luma(path) < grade.BAND_LOW
    assert adjust["exposure"] > 0
    assert grade.BAND_LOW <= after_grade(grade.median_luma(path), adjust) <= grade.BAND_HIGH


def test_a_bright_photo_is_pulled_down_into_the_band(tmp_path):
    path = save(tmp_path, "bright.png", ramp(0.7, 1.0))
    adjust = grade.grade_for(path)

    assert grade.median_luma(path) > grade.BAND_HIGH
    assert adjust["exposure"] < 0
    assert grade.BAND_LOW <= after_grade(grade.median_luma(path), adjust) <= grade.BAND_HIGH


def test_a_photo_already_in_the_band_with_a_full_range_is_left_alone(tmp_path):
    path = save(tmp_path, "mid.png", ramp(0.03, 0.80))
    adjust = grade.grade_for(path)

    assert grade.BAND_LOW <= grade.median_luma(path) <= grade.BAND_HIGH
    assert abs(adjust["exposure"]) <= 0.1
    assert abs(adjust["contrast"]) <= 0.1


def test_a_flat_mid_grey_photo_gets_contrast_but_keeps_its_median(tmp_path):
    path = save(tmp_path, "flat-mid.png", ramp(0.33, 0.47))
    adjust = grade.grade_for(path)

    assert adjust["contrast"] > 0
    assert grade.BAND_LOW <= after_grade(grade.median_luma(path), adjust) <= grade.BAND_HIGH


@pytest.mark.parametrize("grey", [0.0, 0.02, 0.1, 0.25, 0.4, 0.6, 0.8, 0.95, 1.0])
def test_every_flat_image_lands_in_the_band_with_values_the_shader_accepts(tmp_path, grey):
    path = save(tmp_path, f"grey-{grey}.png", np.full((40, 40), grey))
    adjust = grade.grade_for(path)

    assert set(adjust) == {"exposure", "contrast"}
    assert -2.0 <= adjust["exposure"] <= 2.0
    assert -1.0 <= adjust["contrast"] <= 1.0
    # a white or a black frame has no picture to save, but the median still reaches the band
    assert grade.BAND_LOW - 0.01 <= after_grade(grade.median_luma(path), adjust) <= grade.BAND_HIGH + 0.01


def test_the_stretch_never_clips_either_end_of_a_wide_photo(tmp_path):
    """The slope keeps the 0.6th and 99.5th percentiles inside the black and white floors."""
    path = save(tmp_path, "wide.png", ramp(0.0, 0.3))
    adjust = grade.grade_for(path)

    assert after_grade(0.0, adjust) >= -0.01
    assert after_grade(0.3, adjust) <= 1.0


def test_the_result_is_two_rounded_numbers_and_is_repeatable(tmp_path):
    path = save(tmp_path, "dark.png", ramp(0.0, 0.2))
    first = grade.grade_for(path)

    assert first == grade.grade_for(path)
    for value in first.values():
        assert round(value, 3) == value


def test_a_missing_file_is_an_error_not_a_default(tmp_path):
    with pytest.raises(FileNotFoundError):
        grade.grade_for(tmp_path / "nope.jpg")


def test_the_target_band_is_the_one_the_v2_proof_used():
    assert (grade.BAND_LOW, grade.BAND_HIGH) == (0.32, 0.50)
