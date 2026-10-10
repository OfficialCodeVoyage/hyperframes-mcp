"""What the agent is told about the graphic blocks, so it does not read their code to place a layer.

The registry (``assets/motion/registry.json``) has each block's variables and whether it covers the frame or needs a
credit; the row kind of ``compose.COMPONENT_ROWS`` says if it is a picture, a graphic, a label or the finish. What the
registry does not say is what a block is for and where on the frame it draws, so ``PLACES`` does, written from the blocks'
own CSS. A block with no entry fails ``tests/motion/test_block_info.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.compose import COMPONENT_ROWS

# a variable's label is cut to this many characters (a few say the whole JSON form they take)
LABEL_CHARS = 400


@dataclass(frozen=True)
class Place:
    """What a block is for, where it draws on the 1920 x 1080 frame, and a collision or choice worth knowing."""

    use: str
    place: str
    note: str = ""


PLACES: dict[str, Place] = {
    "act-title": Place(
        "A title card that opens an act or chapter: a kicker, the title, an optional subtitle.",
        "The middle of the frame, centred inside the safe margins, on its own night ground.",
    ),
    "bar-grid": Place(
        "A count too big to feel as a number: one small glyph per unit, filled in coloured segments, with a legend.",
        "A dark band across the full width of the frame, centred vertically in the safe zone; the shot behind shows "
        "above and below the band.",
    ),
    "compare-bars": Place(
        "Two to four horizontal bars on one scale, to compare amounts.",
        "Centred in the safe zone on a dark band across the full width of the frame: the title on top, the bars "
        "growing from the left safe margin, an optional note under them.",
    ),
    "counter": Place(
        "A figure that counts up to a target, with its unit and a label.",
        "Centred on the frame inside the safe zone, on a soft dark band across the middle of the frame.",
    ),
    "credit-line": Place(
        "The source credit of a photo or clip: who it comes from, shown while the picture appears.",
        "The bottom-left corner of the safe zone (align right: the bottom-right); one or two lines of small type on a "
        "dark plate.",
        "The lower third and the default date stamp also take the bottom-left corner. Move them (date-stamp position "
        "top-left or top-right, lower-third side right), never shorten their words.",
    ),
    "date-stamp": Place(
        "The date of what the picture shows: a short gold rule and the date on a dark plate.",
        "One corner of the safe zone, chosen by position: bottom-left (the default), top-left, top-right or "
        "bottom-right.",
        "The default corner is where credit-line sits, and lower-third takes it too. When the stamp covers the subject "
        "or another label, change position; never cut its words (it holds them 0.3 s a word, and a shorter slot is "
        "refused).",
    ),
    "finish": Place(
        "Paper dust and fine scratches over the whole video, for the old-film look.",
        "The whole frame, a transparent overlay.",
        "Mount it once over a whole video or act, not on every shot, or the dust pulses at each cut.",
    ),
    "graphic-stamp": Place(
        "A vector graphic over the picture, from an SVG of the project: an icon, arrow or seal that stays sharp, in a "
        "palette colour, entering as a stamp, a draw-on of its strokes or a fade.",
        "A square (small 160, medium 280 or large 440 px) against one corner of the safe zone, or the centre of the "
        "frame; the picture shows around it.",
        "The SVG comes from save_vector and the layer from add_graphic, which puts it above the pictures. The "
        "top-left corner is also the date stamp's, the bottom-left corner the credit line's and the lower third's: "
        "it moves no other layer, so pick a free corner. Gold reads over a dark picture, paper or ink over a bright "
        "one; look at get_frame.",
    ),
    "ledger": Place(
        "A paper ledger sheet that fills row by row, with a total and an optional big gold figure.",
        "A paper sheet at the left inside the safe zone, centred vertically; the big figure to its right, on a soft "
        "dark backing over the right half of the frame.",
    ),
    "lower-third": Place(
        "A person's name and role, on a paper band.",
        "The bottom-left corner of the safe zone (side right: the bottom-right).",
        "Shares the bottom corner with the credit line and the default date stamp.",
    ),
    "map-route": Place(
        "A map shot: land and sea, a camera move, pins, a route drawn on, a year and a caption.",
        "The whole frame; the year and the caption are in the top-left corner of the safe zone, pins and their labels "
        "inside the safe zone.",
    ),
    "newspaper-clipping": Place(
        "A real archival newspaper clipping: source line, dateline, headline, body, an optional photo and a highlight.",
        "A slightly tilted paper cutout inside the safe zone, at the left, centre or right (side); 760 to 1240 px wide, "
        "1560 with a photo.",
        "Only ever a real archival document: sourceLabel is required.",
    ),
    "photo-shot": Place(
        "A full-frame archival photograph with one slow camera move.",
        "The whole frame.",
    ),
    "quote-card": Place(
        "A primary-source quote on a paper card, with its attribution and source.",
        "A paper card centred in the safe zone, over its own night ground.",
    ),
    "split-bar": Place(
        "One whole bar split into labelled parts on one scale, with ranges bracketed under it.",
        "Centred in the safe zone on a dark band across the full width of the frame: labels above the bar, brackets "
        "and an optional note under it.",
    ),
    "video-shot": Place(
        "A full-frame silent clip (image to video) for its slot.",
        "The whole frame; the clip is cropped to cover it, with no letterbox.",
    ),
}


def block_info(component: str | None = None) -> dict[str, Any]:
    """Every block in short (name, use, place, stacking role, variable names), or ``component`` in full: also its title
    and note, and its variables with their types and limits.

    Raises:
        registry.RegistryError: ``component`` is not a block; the message lists them.
    """
    reg = registry_module.default_registry()
    blocks = {n: i for n, i in reg.items.items() if i.type == "hyperframes:block"}
    if component is None:
        return {
            "blocks": [_short(blocks[name]) for name in sorted(blocks)],
            "next": "call block_info with a block's name for its variables with their types and limits",
        }
    if component not in blocks:
        raise registry_module.RegistryError(f"no block {component!r}; the blocks are: {', '.join(sorted(blocks))}")
    item = blocks[component]
    place = PLACES[component]
    return {
        **_short(item, variables=False),
        "title": item.title,
        **({"note": place.note} if place.note else {}),
        "variables": [_variable(variable) for variable in item.variables],
    }


def _short(item: registry_module.Item, *, variables: bool = True) -> dict[str, Any]:
    place = PLACES[item.name]
    entry: dict[str, Any] = {
        "name": item.name,
        "use": place.use,
        "place": place.place,
        "role": _role(item),
    }
    if variables:
        entry["variables"] = [v["id"] for v in item.variables]
    return entry


def _role(item: registry_module.Item) -> str:
    """How the block stacks with the others of its shot (a higher track is in front)."""
    row = COMPONENT_ROWS[item.name][0]
    if item.covers_frame:
        role = "opaque: paints the whole frame and hides every layer under it in its shot; what must show goes on a higher track"
        return role + ("; needs a credit-line layer with text on screen with it" if item.needs_credit else "")
    if row == "Labels":
        return "label: small, over the picture; give it a higher track than any layer that covers the frame"
    if row == "Finish":
        return "topmost: a transparent overlay over everything"
    return "transparent graphic: the picture shows around it; give it a higher track than the picture"


def _variable(variable: dict[str, Any]) -> dict[str, Any]:
    """A variable as the agent needs it: id, type, label, and the limits it has. A default is a sample that never goes
    on screen (the shot list writes every value), so it is not offered."""
    shown: dict[str, Any] = {
        "id": variable["id"],
        "type": variable["type"],
        "label": variable["label"][:LABEL_CHARS],
    }
    for limit in ("maxLength", "min", "max", "unit"):
        if limit in variable:
            shown[limit] = variable[limit]
    if "options" in variable:
        shown["options"] = [option["value"] for option in variable["options"]]
    return shown
