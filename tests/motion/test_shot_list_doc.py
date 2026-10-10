"""The example in docs/motion-shot-list.md is a real shot list: it passes every rule of the composer."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion.compose import plan
from hyperframes_mcp.motion.shotlist import ShotListError, parse_shotlist
from hyperframes_mcp.motion.timing import Word

DOC = Path(__file__).resolve().parents[2] / "docs" / "motion-shot-list.md"


def json_blocks() -> list:
    text = DOC.read_text(encoding="utf-8")
    return [json.loads(block) for block in re.findall(r"```json\n(.*?)```", text, re.DOTALL)]


@pytest.fixture
def example(tmp_path):
    shot_list, alignment = (
        next(b for b in json_blocks() if isinstance(b, dict)),
        next(b for b in json_blocks() if isinstance(b, list)),
    )
    for name in ("audio/voice.wav", "audio/bed.wav", "audio/whoosh.wav"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    words = [Word(w["word"], w["start"], w["end"]) for w in alignment]
    return parse_shotlist(shot_list), words, tmp_path


def test_the_documented_shot_list_passes_every_rule_with_the_documented_alignment(
    example,
):
    shotlist, words, assets = example

    result = plan(shotlist, words, 12.0, assets_dir=assets)

    assert shotlist.meta.publish is True  # a publishing list: no sample values anywhere
    assert [(s.id, s.start, s.end, s.dissolve_s) for s in result.shots] == [
        ("open", 0.0, 5.8, 0.0),
        ("inventory", 5.3, 12.0, 0.5),
    ]
    assert [(l.component, l.start, l.slot) for l in result.layers] == [
        ("act-title", 0.0, 5.8),
        ("lower-third", 0.0, 5.8),
        ("ledger", 5.3, 6.7),
        ("credit-line", 5.8, 4.0),
    ]
    assert result.sfx[0].at == 6.7  # "the gold" is said at 7.0 s, less the 0.3 s offset
    assert result.warnings == []


def test_the_documented_rules_are_enforced_on_the_example(example):
    shotlist, words, assets = example
    raw = shotlist.model_dump()
    raw["shots"][0]["layers"][1]["variables"].pop("role")

    with pytest.raises(ShotListError, match="variable 'role' .*not given"):
        plan(parse_shotlist(raw), words, 12.0, assets_dir=assets)
