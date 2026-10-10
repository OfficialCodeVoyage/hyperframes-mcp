"""What the composer tests share: a registry with stand-ins for the blocks other agents are building, and sample shot lists.

``photo-shot`` and ``map-route`` are not in this tree; the composer is written against the registry, so a fake
registry (tiny block files, the declared variables the brief names) tests the rules that name them.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from hyperframes_mcp.motion import registry

STAND_IN = """<!doctype html>
<html lang="en" data-composition-variables='[]'>
<body><div id="r" data-composition-id="{name}" data-width="1920" data-height="1080" data-start="0" data-duration="5"></div></body>
</html>
"""


def number(name: str, label: str, default: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": name,
        "type": "number",
        "label": label,
        "default": default,
        **extra,
    }


def text(name: str, label: str, default: str = "SAMPLE", **extra: Any) -> dict[str, Any]:
    return {"id": name, "type": "string", "label": label, "default": default, **extra}


def _item(name: str, duration: float, variables: list[dict[str, Any]], **flags: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": "hyperframes:block",
        "title": name,
        "description": f"a stand-in for {name}",
        "license": "Apache-2.0",
        "files": [
            {
                "path": f"blocks/{name}.html",
                "target": f"compositions/{name}.html",
                "type": "hyperframes:composition",
            }
        ],
        "registryDependencies": [],
        "dimensions": {"width": 1920, "height": 1080},
        "duration": duration,
        "variables": variables,
        "source": None,
        "notes": "",
        **flags,
    }


def fake_registry(root: Path) -> registry.Registry:
    """credit-line, photo-shot, video-shot and map-route with the variables the brief names; the files are one-line stand-ins.

    ``photo-shot`` and ``video-shot`` carry the registry flags ``coversFrame`` and ``needsCredit``, as the real ones do.
    """
    grade = {
        "id": "grade",
        "type": "enum",
        "label": "Grade",
        "default": "none",
        "options": [
            {"value": "none", "label": "None"},
            {"value": "warm", "label": "Warm"},
        ],
    }
    items = [
        _item(
            "credit-line",
            6.2,
            [
                number("durationSeconds", "Seconds", 6.2, min=2.4, max=14),
                text("text", "Credit text", maxLength=140),
                {
                    "id": "align",
                    "type": "enum",
                    "label": "Side",
                    "default": "left",
                    "options": [
                        {"value": "left", "label": "Left"},
                        {"value": "right", "label": "Right"},
                    ],
                },
            ],
        ),
        _item(
            "photo-shot",
            6.0,
            [
                number("durationSeconds", "Seconds", 6.0, min=2),
                text("image", "Photo path", ""),
                text("from", "Start of the move", "{}"),
                text("to", "End of the move", "{}"),
                grade,
                number("scrim", "Scrim", 0.0, min=0, max=1),
            ],
            coversFrame=True,
            needsCredit=True,
        ),
        _item(
            "video-shot",
            6.0,
            [
                number("durationSeconds", "Seconds", 6.0, min=1.2, max=60),
                text("video", "Clip path", "", maxLength=200),
                number("trimStart", "Trim start", 0.0, min=0, max=600),
                grade,
                {
                    "id": "hold",
                    "type": "enum",
                    "label": "Hold",
                    "default": "freeze",
                    "options": [
                        {"value": "freeze", "label": "Freeze"},
                        {"value": "loop", "label": "Loop"},
                    ],
                },
            ],
            coversFrame=True,
            needsCredit=True,
        ),
        _item(
            "map-route",
            8.0,
            [
                number("durationSeconds", "Seconds", 8.0, min=4),
                text("region", "Region"),
                text("pins", "Pins as JSON", "[]"),
                text("route", "Route as JSON", "[]"),
                text("yearLabel", "Year"),
                text("caption", "Caption"),
                text("cameraFrom", "Camera start", "{}"),
                text("cameraTo", "Camera end", "{}"),
            ],
        ),
    ]
    for item in items:
        path = root / "blocks" / f"{item['name']}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STAND_IN.format(name=item["name"]), encoding="utf-8")
    return registry.parse_registry({"name": "fake", "items": items}, root)


def photo_layer(**overrides: Any) -> dict[str, Any]:
    variables = {
        "image": "photos/mine.jpg",
        "from": "{}",
        "to": "{}",
        "scrim": 0.2,
        **overrides.pop("variables", {}),
    }
    return {"component": "photo-shot", "at": 0, "variables": variables, **overrides}


def clip_layer(**overrides: Any) -> dict[str, Any]:
    variables = {
        "video": "clips/shot.mp4",
        "trimStart": 0,
        "grade": "none",
        "hold": "freeze",
        **overrides.pop("variables", {}),
    }
    return {"component": "video-shot", "at": 0, "variables": variables, **overrides}


def credit_layer(**overrides: Any) -> dict[str, Any]:
    variables = {
        "text": "US Army Signal Corps, NARA 111-SC-204515",
        **overrides.pop("variables", {}),
    }
    return {"component": "credit-line", "at": 0.3, "variables": variables, **overrides}


def map_layer(**overrides: Any) -> dict[str, Any]:
    variables = {
        "region": "Germany",
        "pins": "[]",
        "route": "[]",
        "yearLabel": "1945",
        "caption": "Map: Natural Earth, public domain",
        "cameraFrom": "{}",
        "cameraTo": "{}",
        **overrides.pop("variables", {}),
    }
    return {"component": "map-route", "at": 0, "variables": variables, **overrides}


def fake_shotlist() -> dict[str, Any]:
    """A valid shot list on the stand-in blocks only (use with ``fake_registry``): a credited photo, then a map.

    p1 runs 0 to 8 s with a credit line over it; m1 cuts in at 8 s and runs to 16 s.
    """
    return {
        "version": 1,
        "meta": {"title": "Stand-in video", "language": "en"},
        "audio": {"voice": {"path": "audio/voice.wav"}},
        "shots": [
            {
                "id": "p1",
                "start": 0,
                "duration": 8,
                "layers": [photo_layer(), credit_layer()],
            },
            {"id": "m1", "start": 8, "duration": 8, "layers": [map_layer()]},
        ],
    }


def sample_shotlist() -> dict[str, Any]:
    """A small valid shot list on the real blocks: an act title, then a ledger with a lower third, then a lower third alone.

    s01 runs 0 to 6 s; s02 dissolves in over 0.5 s at 5.5 s (so s01 stays until 6.0) and runs to 12 s; s03 cuts in at 12 s.
    """
    return {
        "version": 1,
        "meta": {"title": "Test video", "language": "en"},
        "audio": {
            "voice": {"path": "audio/voice.wav"},
            "music": {
                "path": "audio/bed.wav",
                "gain_db": -18.0,
                "duck_under_voice_db": -9.0,
            },
            "sfx": [{"path": "audio/whoosh.wav", "at": 5.5, "gain_db": -6.0}],
            "master": {"lufs": -14.0, "true_peak_db": -1.0},
        },
        "shots": [
            {
                "id": "s01",
                "start": 0,
                "duration": 6,
                "transition_in": {"type": "cut", "seconds": 0},
                "layers": [
                    {
                        "component": "act-title",
                        "at": 0,
                        "variables": {
                            "kicker": "Part 1",
                            "title": "A Test Title",
                            "subtitle": "",
                        },
                    },
                    {
                        "component": "lower-third",
                        "at": 0.3,
                        "variables": {
                            "name": "Jay Cooke",
                            "role": "Financier, 1821-1905",
                            "side": "left",
                        },
                    },
                ],
            },
            {
                "id": "s02",
                "start": 5.5,
                "duration": 6.5,
                "transition_in": {"type": "dissolve", "seconds": 0.5},
                "layers": [
                    {
                        "component": "ledger",
                        "at": 0,
                        "variables": {
                            "title": "Vault inventory",
                            "rows": '[{"label":"Gold bars","value":"10","unit":"bars"}]',
                            "total": '{"label":"Items counted","value":"10"}',
                            "bigFigure": "",
                            "rowTimes": "",
                        },
                    },
                    {
                        "component": "lower-third",
                        "at": 0.2,
                        "variables": {
                            "name": "Second Person",
                            "role": "Banker",
                            "side": "right",
                        },
                    },
                ],
            },
            {
                "id": "s03",
                "start": 12,
                "duration": 6,
                "layers": [
                    {
                        "component": "act-title",
                        "at": 0,
                        "variables": {
                            "kicker": "Part 2",
                            "title": "The End",
                            "subtitle": "",
                        },
                    }
                ],
            },
        ],
    }


def edited(raw: dict[str, Any], *changes: Any) -> dict[str, Any]:
    """A deep copy of ``raw`` with each ``(path, value)`` change applied; a path is a tuple of keys and indexes."""
    out = copy.deepcopy(raw)
    for path, value in changes:
        node = out
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return out
