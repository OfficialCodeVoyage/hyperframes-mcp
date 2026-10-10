"""The tools that tell the agent what a project is, what is on screen when, what is said when and what a graphic block is
for: ``project_info``, ``layer_table``, ``narration`` and ``block_info``."""

from __future__ import annotations

import dataclasses
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from hyperframes_mcp.motion import block_info as blocks
from hyperframes_mcp.motion import edit_config, project_edit, spend
from hyperframes_mcp.motion.compose import COMPONENT_ROWS
from hyperframes_mcp.motion.layer_table import layer_table as table_of
from hyperframes_mcp.motion.layer_table import project_words
from hyperframes_mcp.motion.manifest import file_sha256
from hyperframes_mcp.motion.narration import find_phrase, words_between
from hyperframes_mcp.motion.shotlist import CREDIT_BLOCK
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import resolve_project
from hyperframes_mcp.server.version import server_version

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# a long stretch cuts each layer's narration so the table stays well inside a tool result's size limit
LONG_WINDOW_S = 120.0
SHORT_NARRATION = 160
# the most words one narration answer holds, so it stays well inside a tool result's size limit
MAX_WORDS = 400


def register(server: MCPServer) -> None:
    """Add the context tools to ``server``."""

    @server.tool(annotations=READ_ONLY)
    @reported
    def project_info(project: str | None = None) -> dict[str, Any]:
        """The project this call is about: its folder, profile (with its house rules, which you follow, and the
        edit.yaml they come from), topic, canvas and sources, whether the shot list changed since compose, the spend
        so far against the limits, and the server's version (the git sha it started from, when, and its pid). Call
        it first.
        """
        root, manifest = resolve_project(project)
        sources = {
            name: (str(path), path.exists())
            for name in ("shotlist", "alignment", "assets", "voice")
            if (path := getattr(manifest, name)) is not None
        }
        shotlist = manifest.shotlist
        stale = (
            shotlist is not None
            and shotlist.is_file()
            and manifest.shotlist_sha256 is not None
            and file_sha256(shotlist) != manifest.shotlist_sha256
        )
        return {
            "version": server_version(),
            "project": str(root),
            "profile": manifest.profile,
            "edit_yaml": edit_config.existing_edit_file(manifest.profile),
            "house_rules": edit_config.load_edit_config(manifest.profile).house_rules,
            "topic_id": manifest.topic_id,
            "title": manifest.title,
            "canvas": manifest.canvas.model_dump(),
            "sources": {name: {"path": p, "exists": e} for name, (p, e) in sources.items()},
            "shotlist_changed_since_compose": stale,
            "page_is_the_truth": "index.html holds the video as edited; the shot list is what it was composed from",
            "spend": spend.spend_state(root),
        }

    @server.tool(annotations=READ_ONLY)
    @reported
    def layer_table(
        from_s: float | None = None,
        to_s: float | None = None,
        kinds: list[str] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Every layer on screen in a stretch of the video (all of it by default): start and end in seconds, id,
        kind, file, text (whole), the credit lines that belong to it (a picture's credit, a graphic's source line),
        whether it is AI-made, archival or a graphic, and the narration sentences spoken over it. ``kinds`` keeps only
        those components: photo-shot, video-shot, act-title, quote-card, counter, ledger, bar-grid, compare-bars,
        split-bar, map-route, newspaper-clipping, graphic-stamp, credit-line, date-stamp, lower-third, finish, or any
        other component the page holds (a kind that is neither is refused and the kinds there are are named). The
        credit-line layers are left out unless ``kinds`` names credit-line: each credit is already in the ``credits`` of
        the layer it belongs to. The audio (voice, music, effects) is not a layer here: project_info names the voice
        file and narration gives the words.
        """
        root, _ = resolve_project(project)
        if kinds is not None:
            page = project_edit.mounts((root / "index.html").read_text(encoding="utf-8"))
            known = [*COMPONENT_ROWS]
            known += sorted({m.layer.component for m in page.values()} - set(known))
            if unknown := [kind for kind in kinds if kind not in known]:
                raise project_edit.EditError(
                    f"no layer kind {unknown[0]!r}; the kinds are: {', '.join(known)}. The audio is not a layer: "
                    "project_info names the voice file and narration gives its words"
                )
        rows = table_of(root, words=project_words(root), from_s=from_s, to_s=to_s, kinds=kinds)
        if kinds is None:
            rows = [row for row in rows if row.kind != CREDIT_BLOCK]
        start = min((row.start for row in rows), default=0.0)
        end = max((row.end for row in rows), default=0.0)
        cut = (end - start) > LONG_WINDOW_S
        shown = []
        for row in rows:
            fields = dataclasses.asdict(row)
            if cut and len(fields["narration"] or "") > SHORT_NARRATION:
                fields["narration"] = fields["narration"][: SHORT_NARRATION - 1] + "…"
            shown.append(fields)
        return {
            "project": str(root),
            "layers": shown,
            "count": len(shown),
            "narration": (
                f"cut to {SHORT_NARRATION} characters a layer; ask for a stretch of {LONG_WINDOW_S:g} s or less "
                "for whole sentences"
                if cut
                else "whole sentences"
            ),
        }

    @server.tool(annotations=READ_ONLY)
    @reported
    def narration(
        from_s: float | None = None,
        to_s: float | None = None,
        phrase: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """The narration word by word, with when each word starts and ends (seconds into the video), for the stretch
        from ``from_s`` to ``to_s`` (all of it by default, cut after a few hundred words). Use it to put a counter,
        a title or a picture on the word it belongs to; layer_table only gives the sentence. ``phrase`` instead lists
        every place it is said, each with its first start and last end (case and punctuation do not matter; numbers
        are as the script wrote them, spelled out or in digits: if one finds nothing, try the other form).
        """
        root, _ = resolve_project(project)
        words = project_words(root)
        if words is None:
            raise project_edit.EditError("this project has no narration alignment: its video.project.json names none")
        if from_s is not None and to_s is not None and from_s > to_s:
            raise project_edit.EditError(
                f"from_s ({from_s:g}) is after to_s ({to_s:g}): give the stretch earliest first"
            )
        said = {"project": str(root), "from_s": from_s, "to_s": to_s}
        if phrase is not None:
            try:
                found = find_phrase(words, phrase)
            except ValueError as error:
                raise project_edit.EditError(str(error)) from None
            matches = [
                match
                for match in found
                if (from_s is None or match.end > from_s) and (to_s is None or match.start < to_s)
            ]
            return {
                **said,
                "phrase": phrase,
                "matches": [{"text": m.text, "start": m.start, "end": m.end} for m in matches],
                "note": "numbers are as the script wrote them (spelled out or digits: try the other form when nothing matches); a match is the whole words it covers",
            }
        inside = words_between(words, from_s, to_s)
        shown = inside[:MAX_WORDS]
        return {
            **said,
            "count": len(inside),
            "words": [dataclasses.asdict(word) for word in shown],
            "text": " ".join(word.word for word in shown),
            "truncated": len(inside) > len(shown),
            "note": (
                f"the first {len(shown)} of {len(inside)} words; ask for a shorter stretch"
                if len(inside) > len(shown)
                else "every word said in the stretch"
                if words
                else "the alignment file holds no words at all"
            ),
        }

    @server.tool(annotations=READ_ONLY)
    @reported
    def block_info(
        component: str | None = None,
        layer: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """What the graphic blocks are for, so you need not read their code: each block's use, where it sits on the
        frame (a corner, centred, the whole frame), how it stacks with the other layers of its shot and its variable
        names. Name one block (``component``, e.g. date-stamp) for its variables in full, with their types and limits,
        and what to watch for when you place it. Name a layer instead (``layer``, e.g. l-price-now-7) for its block in
        full, with what each variable is set to on that layer now (``now``).
        """
        if layer is None:
            return blocks.block_info(component)
        root, _ = resolve_project(project)
        found = project_edit.mounts((root / "index.html").read_text(encoding="utf-8"))
        if layer not in found:
            raise project_edit.EditError(f"no layer {layer!r} in index.html")
        placed = found[layer].layer
        if component not in (None, placed.component):
            raise project_edit.EditError(f"{layer} is a {placed.component}, not a {component}")
        info = blocks.block_info(placed.component)
        info["layer"] = {"id": layer, "start": placed.start, "end": placed.end}
        for variable in info["variables"]:
            variable["now"] = placed.values.get(variable["id"])
        return info
