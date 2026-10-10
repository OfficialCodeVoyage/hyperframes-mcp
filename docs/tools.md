# Tools

The 30 tools of the `hyperframes-edit` server. This page is generated from the server's own tool list by `scripts/gen_tools_doc.py`; do not edit it by hand (`python scripts/gen_tools_doc.py` rewrites it, `--check` fails when it is stale).

## Look

Read the project. Nothing here changes it.

### `ping`

Whether the server runs, which code (git sha, start time, pid), where, and which keys it can see (names
only, never values).

### `project_info`

The project this call is about: its folder, profile (with its house rules, which you follow, and the
edit.yaml they come from), topic, canvas and sources, whether the shot list changed since compose, the spend
so far against the limits, and the server's version (the git sha it started from, when, and its pid). Call
it first.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `project` | string or null | no |  |

### `layer_table`

Every layer on screen in a stretch of the video (all of it by default): start and end in seconds, id,
kind, file, text (whole), the credit lines that belong to it (a picture's credit, a graphic's source line),
whether it is AI-made, archival or a graphic, and the narration sentences spoken over it. ``kinds`` keeps only
those components: photo-shot, video-shot, act-title, quote-card, counter, ledger, bar-grid, compare-bars,
split-bar, map-route, newspaper-clipping, graphic-stamp, credit-line, date-stamp, lower-third, finish, or any
other component the page holds (a kind that is neither is refused and the kinds there are are named). The
credit-line layers are left out unless ``kinds`` names credit-line: each credit is already in the ``credits`` of
the layer it belongs to. The audio (voice, music, effects) is not a layer here: project_info names the voice
file and narration gives the words.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `from_s` | number or null | no |  |
| `to_s` | number or null | no |  |
| `kinds` | list of string or null | no |  |
| `project` | string or null | no |  |

### `narration`

The narration word by word, with when each word starts and ends (seconds into the video), for the stretch
from ``from_s`` to ``to_s`` (all of it by default, cut after a few hundred words). Use it to put a counter,
a title or a picture on the word it belongs to; layer_table only gives the sentence. ``phrase`` instead lists
every place it is said, each with its first start and last end (case and punctuation do not matter; numbers
are as the script wrote them, spelled out or in digits: if one finds nothing, try the other form).

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `from_s` | number or null | no |  |
| `to_s` | number or null | no |  |
| `phrase` | string or null | no |  |
| `project` | string or null | no |  |

### `block_info`

What the graphic blocks are for, so you need not read their code: each block's use, where it sits on the
frame (a corner, centred, the whole frame), how it stacks with the other layers of its shot and its variable
names. Name one block (``component``, e.g. date-stamp) for its variables in full, with their types and limits,
and what to watch for when you place it. Name a layer instead (``layer``, e.g. l-price-now-7) for its block in
full, with what each variable is set to on that layer now (``now``).

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `component` | string or null | no |  |
| `layer` | string or null | no |  |
| `project` | string or null | no |  |

### `style_info`

The profile's picture style and settings: the full prompt frame a new picture is sent in (``<subject>``
marks where the request goes), its house rules (follow them), the picture quality and format a quote uses,
and the search preferences. ``style_references`` are stills for a later, paid option and are not used yet.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `project` | string or null | no |  |

### `spend_status`

What this video has spent against its limits: this video's total and today's, every project's spend today,
and what is left under each limit.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `project` | string or null | no |  |

## Talk to the app

Work with what the person is doing in the HyperFrames desktop app. These need the app running.

### `resolve_selection`

What the person is pointing at, as what is on screen there. Pass the ``<app_context>`` block of their
message (a picked element, a timeline range, marks they drew, or only where the playhead stood), or
``use_app`` to ask the app what is picked now. Gives the time or range, the layers on screen then (id, kind,
file, text, credits), the shots, the narration sentence spoken and the credit lines shown. Each layer the pick names
comes again under ``picked_layers`` with its own time, file, credits and narration (a pick made on the timeline
carries the playhead's moment, so the picked clip is often not on screen there).

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `app_context` | string or null | no |  |
| `use_app` | boolean | no |  |
| `project` | string or null | no |  |

### `catch_up`

What changed in the project since you last looked, and who changed it. With the app running: its history
entries (who, when, label, files; ``since`` is an entry id, an ISO time or ``mine``, the default: after your
own last entry) and the runs of its own agent. With the app closed: the project's git log and the files
written since (``since`` an ISO time, default the last 24 hours). Call it at the start of a turn, before you
edit, so you never write over the person's changes.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `since` | string or null | no |  |
| `project` | string or null | no |  |

### `post_comment`

Pin a note at ``time`` (seconds) on the person's timeline in the app, for what you found while checking
(a credit that covers a face, a title that comes late). It waits in the app's chat until the person
addresses it. Needs the app running: when it is not, nothing is saved and you tell the person in your reply.

When the person presses Apply on a comment, the app starts a fresh agent session that sees only the comment's
text. So for a comment that asks for a change, say what to change in words that stand alone, name the ``layer``
(its id, which must be on the page) and set ``change`` to ``free`` (made with the free tools) or ``paid`` (a
new picture or clip: the comment then tells that agent to quote first and not pay before the person's yes). A
finding alone is ``none``; a ``layer`` is named in the comment whatever ``change`` is.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `time` | number | yes |  |
| `body` | string | yes |  |
| `layer` | string or null | no |  |
| `change` | 'none' \| 'free' \| 'paid' | no |  |
| `project` | string or null | no |  |

### `list_comments`

The comments on the person's timeline (yours and theirs), newest first: number, time, status (``open``,
``in_progress``, ``addressed``, ``declined`` or ``resolved``), the text and the reply. ``status``
keeps only those. Needs the app running.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `status` | string | no |  |
| `project` | string or null | no |  |

### `resolve_comment`

Mark a comment on the person's timeline resolved (``comment`` is its number from list_comments, e.g. 4), or
open it again with ``reopen``. The comment is kept, not deleted. Use it only when the person asks, or for a
finding of your own that is settled. Never for a comment you were asked to apply: the app marks that one
addressed when your run ends and the person resolves it, and resolving it while you work ends your run, so your
answer is lost. A comment the app's agent is working on or has queued is refused. Needs the app running.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `comment` | integer or string | yes |  |
| `reopen` | boolean | no |  |
| `project` | string or null | no |  |

### `get_frame`

The frame of the video at ``time`` seconds as an image, from the HyperFrames app's own preview: the app must
be running with the project open. Nothing is rendered or written. Look at it after an edit instead of cutting
frames yourself.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `time` | number | yes |  |
| `project` | string or null | no |  |

### `contact_sheet`

One image of ``n`` frames (up to 16) spread evenly from ``from_s`` to ``to_s`` seconds, each labelled with
its time, left to right and top to bottom: to review a stretch at a glance. The frames are the app's preview, one
after another (about 2 to 3 seconds each), so ask for as few as the review needs; the app must be running with
the project open.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `from_s` | number | yes |  |
| `to_s` | number | yes |  |
| `n` | integer | no |  |
| `project` | string or null | no |  |

## Edit

Change the project. Each edit is one history entry in the app, under the author `hyperframes-mcp`.

### `edit_timeline`

Move, trim, split, duplicate or delete layers in one batch, through HyperFrames' own timeline edit. ``ops`` are
{"verb": "move", "layer": "l-vault-1", "time": 12.5} (time: seconds, "+2"/"-2" from its start, or
"after:l-vault-2"), {"verb": "trim", "layer": ..., "start"/"end"/"duration": ...}, {"verb": "split", "layer": ...,
"time": ...}, {"verb": "duplicate", "layer": ..., "at": ...} and {"verb": "delete", "layer": ...}, layers by
the ids layer_table gives. ``dry_run`` (the default) writes nothing and says what each layer would become;
look at it, then call again with dry_run=false. A duplicate pushes the later layers of its track on (they are
listed as pushed). A split or duplicate makes a layer under the next free id of its shot. A trimmed layer's
block plays inside its new length. One history entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `ops` | list of object | yes |  |
| `dry_run` | boolean | no |  |
| `project` | string or null | no |  |

### `set_variable`

Change one variable of one layer's block: its text, a number, a colour, a position or another option
(block_info lists each block's variables with their types and limits). The value is checked against the
block's own variable; a name or value it does not take is refused, as is a change that would leave a
picture without a credit line. The layer's length is set with edit_timeline and its file with replace_media.
One history entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `layer` | string | yes |  |
| `name` | string | yes |  |
| `value` | string or number or boolean | yes |  |
| `project` | string or null | no |  |

### `add_block`

Add one more layer to the video: any block that needs no file (counter, ledger, bar-grid, compare-bars, split-bar,
act-title, quote-card, credit-line, date-stamp, lower-third; a vector graphic is add_graphic, a picture or clip
is replace_media). It shows from ``start`` for ``duration`` seconds (block_info gives each block's limits).
``values`` must give every text and number variable (block_info names them, with types and limits): a block's
default is a sample and never goes on screen, so write the value, an empty string when it is not used; the
others (colours, options) take the block's defaults. The layer joins the shot on screen at ``start`` under the next id of that shot (l-<shot>-<n>) and is
put in front of every layer it shares time with; the answer warns when its text is too long for the time given
or when it now hides another layer. Place it with the narration in mind (narration gives word times), then look
at it with get_frame and run_checks. One history entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `component` | string | yes |  |
| `start` | number | yes |  |
| `duration` | number | yes |  |
| `values` | object or null | no |  |
| `project` | string or null | no |  |

### `arrange_layer`

Move or resize one layer (a counter, ledger, chart, title, date stamp, lower third, graphic or credit line; not
a picture or clip, which fill the frame). ``dx`` and ``dy`` are pixels of the 1920x1080 canvas, right and down, from
where the block puts itself (-100 moves it 100 px left); ``scale`` is a factor around the frame's centre (0.2 to
2, so 0.8 makes it smaller and a little more central). What you leave out stays as it is, and ``reset`` puts the
layer back where its block puts it. Nothing knows where a block draws its words, so look at it with get_frame at
a time it is on screen (also where a credit line or date stamp shows with it) and nudge again. One history
entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `layer` | string | yes |  |
| `dx` | number or null | no |  |
| `dy` | number or null | no |  |
| `scale` | number or null | no |  |
| `reset` | boolean | no |  |
| `project` | string or null | no |  |

### `replace_media`

Put a picture or clip (``file``) in the place of one photo-shot's or video-shot's layer (``layer``, e.g.
l-vault-1) and set the credit line on screen with it. A file made by confirm_spend or fetched by fetch_media
brings its own credit (its record); any other needs ``credit`` (who made it, shown on screen). ``credit_layer`` picks the credit line when more
than one is on screen with the layer. ``file`` must be in the project; it is copied into assets/media/edits/
unless it is there already. One history entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `layer` | string | yes |  |
| `file` | string | yes |  |
| `credit` | string or null | no |  |
| `credit_layer` | string or null | no |  |
| `project` | string or null | no |  |

### `add_graphic`

Add one vector graphic to the video as a new layer: ``vector`` is a name saved by save_vector (or a path of the
project to an SVG), shown from ``start`` for ``duration`` seconds (at least 2.4). ``position``: top-left,
top-right, bottom-left, bottom-right or center, inside the safe zone (a credit line sits in the bottom corner on its
side and a date stamp in the corner it names: the answer warns when the graphic shares a corner with one showing
then, so choose another corner or time; this moves no other layer). ``size``: small, medium or large.
``animation``: stamp (scales down onto the frame), draw-on (strokes draw themselves) or fade. ``color``: a palette
colour for what the SVG paints with currentColor (gold, gold_dim, paper, paper_aged, ink, oxblood, verdigris,
slate, loss, gain; default gold). The layer joins the shot on screen at ``start`` and is put in front of every
layer it shares time with. Gives the new layer's id and time; look at it with get_frame, then run_checks. One
history entry of yours; undo takes it back.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `vector` | string | yes |  |
| `start` | number | yes |  |
| `duration` | number | yes |  |
| `position` | string | no |  |
| `size` | string | no |  |
| `animation` | string | no |  |
| `color` | string or null | no |  |
| `project` | string or null | no |  |

### `undo`

Take back your own last ``steps`` edits (the history entries of hyperframes-mcp), newest first. It never touches
the person's: when they changed a file you edited after you did, it stops and says so. Says which entries it
undid and how many of yours are left.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `steps` | integer | no |  |
| `project` | string or null | no |  |

### `run_checks`

Check the page after an edit, as the app's own check cannot (it samples a few moments of the whole video
and can miss the edited spot): every picture has a true credit line on screen with it, no layer is hidden
behind one that covers the frame (a credit above all), and the HyperFrames lint. Nothing is drawn: to see the
result, call get_frame. Gives each finding's severity, layer id, time and what to fix, errors first, and one ok
line when there is nothing.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `project` | string or null | no |  |

## Free media

Photos from the free archives, and vectors. Nothing here is paid.

### `search_media`

Search the free archives (Library of Congress, Wikimedia Commons, Internet Archive, Openverse) for a picture of
``query``; nothing is paid. A handful of hits (``limit``, up to 10): a short ``id``, title, source, licence, the
credit text for the screen, size and date. Only CC0 and public domain are found; ``allow_by=true`` adds CC BY
(its credit names who made it); share-alike and non-commercial are never found. The profile's edit.yaml
sets the sources and whether CC BY is allowed when ``allow_by`` is left out. ``kind`` is ``photo``: a ``clip``
finds nothing, because these archives hold still pictures only. When nothing usable comes back it asks again,
within a few seconds, the sources that did not fail, with fewer words (names and years first): ``retried`` lists
those wordings and ``tried`` shows every round. Put a hit in the project with fetch_media.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | yes |  |
| `kind` | string | no |  |
| `limit` | integer | no |  |
| `allow_by` | boolean or null | no |  |
| `project` | string or null | no |  |

### `fetch_media`

Download the hit ``id`` of a search_media (this project's latest hundred) into assets/media/found/<name> with
its licence record beside it, and look at it: the result carries a small preview. ``name`` is the new file's name
without suffix (the suffix is the picture's own). Then replace_media puts it in a layer and takes its credit
from the record.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes |  |
| `name` | string | yes |  |
| `project` | string or null | no |  |

### `search_icons`

Find icons in the free sets kept in the repository (Maki CC0, Tabler MIT, Material Symbols Apache-2.0: coins,
bank, ship, train, factory, anchor, scale, document, seal, chart arrows, map pins, flags, people, tools and so on) by
name and tags; every word of ``query`` must match. Each hit gives its id, set, tags, licence and file, and an
image shows them numbered in that order: look at it before you pick, because a name can mislead (Maki's "bank" is
a banknote). Put one in the project with save_vector, giving its id.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `query` | string | yes |  |
| `limit` | integer | no |  |

### `save_vector`

Save a vector in the project as assets/media/vectors/<name>.svg. ``svg`` is either the markup you wrote
(it must start with <svg xmlns="http://www.w3.org/2000/svg" viewBox="...">) or an icon id from search_icons. The
file is checked first: drawing elements only (no script, style, text, image, filter or link), no reference
outside the file, a viewBox, at most 100 KB and 1000 elements, and colours from the profile's palette (a refusal
lists it) or currentColor, which add_graphic recolours: prefer currentColor for a single-colour mark. A refusal
lists every problem. A name that exists is not overwritten. ``name``: letters, digits, - and _ (up to 64).

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `svg` | string | yes |  |
| `name` | string | yes |  |
| `project` | string or null | no |  |

### `vectorize`

Trace a picture of the project (``image``, a path in the project) into a vector on this machine, free, with
vtracer, and save it as assets/media/vectors/<name>.svg after the same check as save_vector. ``colors``:
mono (one shape colour, currentColor, the best for a seal, a silhouette or an engraved mark) or palette (the
picture's colours snapped to the palette). A simple picture with a plain light ground traces well; a photograph
gives far too many shapes. If vtracer is not installed the answer says how to install it.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `image` | string | yes |  |
| `name` | string | yes |  |
| `colors` | string | no |  |
| `project` | string or null | no |  |

## Paid pictures and clips

Quote first, then confirm. The limits are enforced by the server: see `docs/spend-safety.md`.

### `quote_picture`

Quote one new picture of ``request`` in the profile's style (nothing is paid): its price and the full
prompt it would send. Describe only the subject, place, era and framing: the style is added. ``quality`` is
medium or high (default: the profile's picture_quality). ``name`` is the new file's name without suffix; it
goes to assets/media/edits/ as a JPEG (a PNG when the profile's picture_format says so) at the video's
canvas size.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `request` | string | yes |  |
| `name` | string | yes |  |
| `quality` | string or null | no |  |
| `project` | string or null | no |  |

### `quote_clip`

Quote one clip made from the picture ``still`` (a path in the project); ``motion`` says what moves and how the camera moves. ``seconds`` is 4, 6 or 8 (the lengths the clip model makes: any other is refused). Nothing is paid; the clip comes at the video's size.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `still` | string | yes |  |
| `motion` | string | yes |  |
| `name` | string | yes |  |
| `seconds` | integer | yes |  |
| `project` | string or null | no |  |

### `confirm_spend`

Make the picture or clip a quote describes (a paid call) and record the spend. Refused when the quote
expired or changed, or when the call would pass the per-call or per-video limit. Confirming the same quote
twice pays once. A picture comes with a small preview image: look at it before putting the file in a layer
with replace_media.

| Argument | Type | Required | Meaning |
|---|---|---|---|
| `quote_id` | string | yes |  |
| `project` | string or null | no |  |
