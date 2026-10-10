# The shot list and the composer

A **shot list** is the JSON an agent writes to say what is on screen when. It names blocks of the motion library
(`docs/motion-library.md`) and fills their declared variables; it never contains animation code. The
**composer** (`src/hyperframes_mcp/motion/compose.py`) turns it, the narration's word timings and a folder of assets into a
HyperFrames project, and `python -m hyperframes_mcp.motion build` renders and masters that project into a finished MP4.
Everything runs offline; the commands are listed under "Commands". The server's own tools, which edit a composed project
while a person works in the HyperFrames desktop app, are in `docs/tools.md`.

## The format

A complete small example: an act title with a lower third, a dissolve into a ledger with a credit line, music under the
voice and one sound effect. The times are cues, so a different take of the same script moves every cut by itself.

```json
{
  "version": 1,
  "meta": {"title": "The Bundesbank gold, cold open", "language": "en", "publish": true},
  "audio": {
    "voice": {"path": "audio/voice.wav"},
    "music": {"path": "audio/bed.wav", "gain_db": -18.0, "duck_under_voice_db": -9.0},
    "sfx": [{"path": "audio/whoosh.wav", "at": {"cue": "the gold", "offset": -0.3}, "gain_db": -6.0}],
    "master": {"lufs": -14.0, "true_peak_db": -1.0}
  },
  "shots": [
    {
      "id": "open",
      "start": 0,
      "duration": {"cue": "salt mine"},
      "layers": [
        {"component": "act-title", "at": 0,
         "variables": {"kicker": "Part 2", "title": "The salt mine", "subtitle": ""}},
        {"component": "lower-third", "at": 0,
         "variables": {"name": "Dwight Eisenhower", "role": "Supreme Allied Commander", "side": "left"}}
      ]
    },
    {
      "id": "inventory",
      "start": {"cue": "salt mine", "offset": -0.5},
      "duration": 6.7,
      "transition_in": {"type": "dissolve", "seconds": 0.5},
      "layers": [
        {"component": "ledger", "at": 0,
         "variables": {"title": "Bundesbank reserve",
                       "rows": "[{\"label\":\"Gold bars\",\"value\":\"8,198\",\"unit\":\"bars\"}]",
                       "total": "{\"label\":\"Items counted\",\"value\":\"8,198\"}",
                       "bigFigure": "", "rowTimes": ""}},
        {"component": "credit-line", "at": 0.5, "duration": 4.0,
         "variables": {"text": "Bundesbank inventory, 1945. National Archives.", "align": "right"}}
      ]
    }
  ]
}
```

The narration's word timings it was written against (the alignment, see "Cues"):

```json
[
  {"word": "On", "start": 0.2, "end": 0.4}, {"word": "April", "start": 0.5, "end": 0.8},
  {"word": "twelfth", "start": 0.9, "end": 1.4}, {"word": "the", "start": 3.0, "end": 3.1},
  {"word": "generals", "start": 3.3, "end": 3.8}, {"word": "rode", "start": 3.9, "end": 4.2},
  {"word": "down", "start": 4.6, "end": 4.8}, {"word": "into", "start": 5.0, "end": 5.2},
  {"word": "a", "start": 5.3, "end": 5.4}, {"word": "salt", "start": 5.8, "end": 6.1},
  {"word": "mine.", "start": 6.2, "end": 6.7}, {"word": "The", "start": 7.0, "end": 7.2},
  {"word": "gold", "start": 7.3, "end": 7.5}, {"word": "was", "start": 7.6, "end": 7.8},
  {"word": "counted.", "start": 8.0, "end": 8.6}
]
```

| Field | Meaning |
|---|---|
| `version` | always 1 |
| `meta` | `title`, `language` (`en`), and `publish` (default false: see "Rules") |
| `audio.voice` | the narration file; every path is relative to the assets folder |
| `audio.music` | optional bed: `gain_db` (its level) and `duck_under_voice_db` (how far it drops while the voice speaks, zero or negative) |
| `audio.sfx` | sound effects: a file, the time `at`, `gain_db` |
| `audio.master` | the delivery loudness, default -14.0 LUFS integrated and at most -1.0 dBTP |
| `shots` | in time order; each has an `id` (letters, digits, `_`, `-`), a `start`, a `duration`, an optional `transition_in` and its `layers` |
| layer | `component` (a block of the registry), `at` (seconds into the shot, default 0), optional `duration` (default: to the end of the shot), optional `track` (the HyperFrames Studio row and the stacking order inside the shot; default the layer's position), `variables`, and `sample` (see "Rules") |

The models are closed: a key that is not in this table is an error. What a block takes is its `variables` list in
`assets/motion/registry.json` (the package's `src/hyperframes_mcp/assets/motion` folder; `python -m hyperframes_mcp.motion show NAME`).

## Time, cues and slots

A **time** is a number of seconds or a **cue**: `{"cue": "salt mine", "occurrence": 1, "offset": 0.0}`, the moment the
narration starts saying that phrase, plus `offset` seconds. The phrase is matched against the alignment, a JSON list of
`{word, start, end}` (`src/hyperframes_mcp/motion/timing.py`): on consecutive words, ignoring case, punctuation and hyphens, so `8,198` and
`single-cable` match however the tool split them. `occurrence` picks the second or third time a phrase is said. A phrase that
is not said, or is said fewer times, is an error that says so. As a shot's `duration`, a cue is the moment the shot **ends**.

The alignment is a file you bring; the composer does not make one. It is a JSON list of `{word, start, end}` (seconds into the
voice, starts never going backwards), made by whatever aligner you use from the finished narration.

**Slots.** A layer starts at its shot's start plus `at` and ends at the end of its shot, or after its own `duration`. That length
is the layer's **slot**. The composer writes it twice, as the mount's `data-duration` and as the block's `durationSeconds`,
so the two are the same number by construction; a shot never sets `durationSeconds`. A slot shorter than the block's own
minimum (the `min` of its `durationSeconds`, its fades plus the reading time) is an error; one over its `max` is a warning, and
so is a layer that ends after its shot. For ten blocks the minimum depends on the layer's own text and the composer works it out
(see "The preflight"); the static `min` is only the floor.

**Cuts and dissolves.** A shot cuts in by default. `"transition_in": {"type": "dissolve", "seconds": 0.5}` crossfades from the
shot before it (the first shot fades in from the night ground): the incoming shot's layers fade up on the enter ease and
the outgoing shot's down on the exit ease, over the dissolve length of the tokens (0.5 to 1.0 s), starting at the incoming shot's
`start`. The shot before must therefore reach the dissolve: it ends between the incoming `start` and `start` plus the dissolve
length, and the composer keeps it on screen until the dissolve is over. Any other overlap is an error.

## Rules, and why

Every message names the shot, the layer and the variable. All problems are reported together; nothing is written when there is one.

| Rule | Why |
|---|---|
| The component is in the registry and is a block; its variables are declared by it | the composer is written against the registry, so a new block needs no composer change |
| `durationSeconds` is never set by a shot | a hand-typed duration can disagree with the slot and cut a fade |
| Every string and number variable except `durationSeconds` is written out by the shot (an empty string where the block treats it as absent). Enums, booleans and colours may default | a block's defaults are samples ("Jay Cooke", 8,198 bars). A forgotten field would put an invented figure on screen in a published video |
| `"sample": true` on a layer lifts that rule, for a gallery or a test; a shot list whose `meta` has `"publish": true` refuses it | the escape hatch cannot reach a video |
| Values have the declared type, text at most `maxLength`, numbers inside `min` and `max`, enums one of the options | the block would throw at render time, minutes later |
| A slot is at least the block's minimum | a block whose slot is too short throws |
| A slot is at least the shortest the block can run for its text: `credit-line`, `date-stamp`, `lower-third`, `act-title`, `quote-card`, `counter`, `ledger`, `bar-grid`, `split-bar` and `compare-bars` work it out from the layer's variables (the preflight, below) | these blocks hold their text for its reading time (0.3 s a word) and throw when the slot is shorter, which only a render of a few minutes used to show |
| A layer that a full-frame layer above it covers is a warning; a picture or clip whose credit line is covered is an error (the preflight, below) | `act-title`, `quote-card`, `photo-shot`, `video-shot` and `map-route` paint an opaque ground over the whole frame, and a higher track is in front: whatever is under one is never seen |
| Shots are in time order and overlap only through a dissolve; a dissolve is 0.5 to 1.0 s; a cut has no length | a stray overlap is two shots fighting for the screen |
| Shot starts and sound effects are inside the voice's length, when it is known (`build` asks ffprobe) | a time after the narration ended is a typo |
| A layer of a block whose registry entry says `needsCredit` (`photo-shot`, `video-shot`) needs a `credit-line` layer in the same shot with non-empty `text` that is on screen at the same time | the provenance of an archival photograph, and the "AI-generated illustration" line of an AI clip, are on screen with the picture (the rule names the `credit-line` block and reads the flag of the other one) |
| A `video` variable names a readable video (ffprobe finds a video stream); its `trimStart` is before the end of the clip. A slot longer than the clip after `trimStart` is a warning that names `hold` | a clip that cannot be played, or a trim past its end, fails the render minutes later; a slot that outlasts the clip freezes its last frame or loops it, which the shot list should mean |
| An asset path is relative, with no `..`, no backslash, no URL and no drive letter, and stays inside the assets folder (links are followed) | a shot list is written by an agent and must not read the rest of the disk |

**Assets.** A variable called `image`, `video` or `svg` holds a path to a file in the assets folder. The composer copies the file to
`assets/media/<same path>` in the project and rewrites the variable to that project path, which is what the blocks accept. The
audio paths are checked and copied the same way. A clip's own sound is never mixed: only the files under `audio` are. Everything else a block needs (GSAP, `tokens.css`, the fonts) comes with its
install, once per project.

**Repeated blocks.** A shot list may mount one block as often as it likes, at the same moment or not; the block file is installed
once and each mount has its own element id, slot and variables. The pinned HyperFrames keeps a timeline per mount, so no per-mount
copy of a block file is needed (see "What was verified").

## The preflight

Two checks of `src/hyperframes_mcp/motion/preflight.py` run inside the composer's plan, so `build --check`, `compose` and `build` all run them in
about a second, before anything is written or rendered. An error stops (exit code 2, nothing written); a warning prints.

**Minimum slots.** A block that holds its text for the reading time cannot run in a shorter slot. Each of the ten blocks above
exports its own `minSlot(V, tok)` between its `// logic:begin` and `// logic:end` lines (the design is in
`docs/motion-library.md`, "The slot minimum comes from the block"). The preflight runs that text in Node for every
such layer, with the variables the layer carries and the values of `tokens.css`.

```
shot 'weber', layer 2 (credit-line): a slot of 3.5 s is shorter than the 3.90 s credit-line needs for its text (entrance, the
reading time of its words, exit); give the layer at least 3.9 s or shorten the text
shot 'ledger', layer 1 (ledger): ledger: rowTimes lists 1 times for 2 rows (the block would throw at render time)
map-route (shot 'locator', 'rail'): not pre-checked: its shortest slot is the camera move, ...; only the static minimum is checked
```

- The first says how long the layer must be, to the hundredth of a second: give it that time (a layer may be shorter than its shot
  with `duration`) or shorten the text.
- The second is a variable the block itself refuses whatever the slot (an empty credit, row times that are not in order); the block's
  own message is quoted.
- The third is a note, never silence: `map-route` and `newspaper-clipping` have a shortest slot that is not computed before a render
  (the registry gives the reason in `slotNotChecked`), so only the static `min` of their `durationSeconds` is checked for them.
  A render can still fail on those two when the slot is too short.
- Without Node on the machine the plan says so in one warning and checks only the static minimum.
- What it does not check: the layout of a block (a ledger with more rows than fit, a label too long for its column), which only a
  render shows.

**Hidden layers.** A block whose registry entry says `coversFrame` paints an opaque ground over the whole frame: `act-title`,
`quote-card`, `photo-shot`, `video-shot` and `map-route` do (`counter`, `ledger`, `bar-grid`, `newspaper-clipping`, the lower third, the date stamp, the credit
line and `finish` leave most of the frame to what is under them). Inside one shot a layer on a higher `track` is in front, and on the
same track the later layer of the list is. For every layer the preflight takes the time its slot shares with the full-frame layers in
front of it (two covering layers count once for the time they share) and, when that is 0.3 s or more, warns:

```
shot 'weber', layer 3 (lower-third) is hidden for 3.06 s of its 5.80 s behind layer 4 (quote-card), which paints the whole frame above it
(a higher track is in front); give it a higher track or end it sooner
shot 'swiss-map', layer 2 (credit-line) is hidden for all of its 5.00 s and never shows: layer 1 (map-route) paints the whole frame above it
```

The slot window is used, not the fades, so a layer that is only half faded in still counts as seen.

The one error is the credit of a picture or clip (a block with `needsCredit`: `photo-shot`, `video-shot`). A credit line counts for it
while the two can both be seen: if hiding leaves them on screen together for under 0.3 s (while their slots share more), the
picture has no credit in sight:

```
shot 'weber', layer 1 (photo-shot): its credit line (layer 2 (credit-line)) is hidden: it is on screen with the picture for only 0.10 s
of the 5.50 s their slots share, because layer 3 (quote-card) paints the whole frame above them; put the credit line on a higher track
than that layer, or start that layer later
```

A card that comes in after the photograph has been seen with its credit for a few seconds (the credit and the photograph hidden
together from then on) is not an error; each hidden layer is a warning, as above. Shots do not hide one another: a dissolve fades one
shot into the next, which this check does not model.

## Bars in a shot list: `split-bar` and `compare-bars`

Two blocks that show a share or a ratio (what they draw, every rule and the layout: `docs/motion-library.md`,
"split-bar and compare-bars"). In a layer their variables are plain JSON text for `parts`, `markers` and `bars`, and numbers for
`whole` and `scaleMax`; a layer writes all of them out (`markers` as `"[]"` and `note` as `""` for none).

```json
{
  "component": "split-bar",
  "at": 0.4,
  "duration": 12.0,
  "variables": {
    "wholeLabel": "The Bundesbank's gold ledger",
    "wholeValue": "about 791 tonnes",
    "whole": 791,
    "parts": "[{\"label\":\"found in Germany\",\"display\":\"about a third\",\"value\":236,\"tone\":\"gold\",\"at\":1.0,\"seconds\":1.2},{\"label\":\"went abroad\",\"display\":\"about two thirds\",\"value\":503,\"tone\":\"ember\",\"at\":2.6,\"seconds\":1.6}]",
    "markers": "[{\"label\":\"the Marburg pile\",\"display\":\"about a quarter of the ledger\",\"from\":0,\"to\":210,\"at\":5.0}]",
    "note": ""
  }
}
```

- **Times are seconds into the layer's slot**, not into the shot or the video and not cues: a part that should start growing when the
  narrator says "two thirds" has `at` equal to that cue's time minus the layer's start (the time is in your alignment file, and the server's `narration` tool gives it too).
  `at` and `seconds` of a part, a bar or a marker may not be negative; a part or bar needs at least 0.3 s to grow.
- **The slot** is the shortest the block can run for its parts: the latest of the heading, the note and every part (`at` + `seconds`
  + 0.7 s for its label to arrive + 1.2 s and 0.3 s a word of reading) or bracket (`at` + 0.7 + reading), plus the 0.5 s exit. The
  example above needs 10.1 s (the bracket is read until 9.6 s); `compare-bars` with the example bars of the library needs 8.5 s. A
  shorter layer is an error that names the number:

```
shot 'gold', layer 1 (split-bar): a slot of 9 s is shorter than the 10.10 s split-bar needs for its text (entrance, the reading time of its words, exit); give the layer at least 10.1 s or shorten the text
```

- **Layout errors come at plan time too.** A label that cannot be placed (`split-bar`: a part of one percent between two long labels, no
  free place above or below the bar), a text that fits neither inside its bar nor beside it (`compare-bars`), two overlapping
  brackets, a heading or note too wide for one line and a graphic too tall for the safe zone are errors of the block, quoted by the
  preflight, so they are found in about a second and not after a render.
- **Over a photograph.** Both blocks sit on a soft backing of the scrim token and cover nothing else: they are not `coversFrame`.

## The page and the audio

The root `index.html` is 1920x1080 at 24 fps, from the tokens, on the night ground. Every layer is mounted directly in the
root (`data-composition-src`, `data-start`, `data-duration`, `data-track-index`, `data-timeline-label`, a JSON-escaped
`data-variable-values`) with its shot's class, `shot-<id>`. There is no wrapper per shot: HyperFrames Studio draws each child
of the root as a clip, so a wrapper would be one clip the whole video long. One `z-index` stack orders the layers: a later
shot is in front of an earlier one, and within a shot the higher `track` (the later layer when two share one). A dissolve is a
root-level paused GSAP timeline, registered like any other, that tweens the opacity of the two shots' layer mounts and nothing
else: never a graded picture inside a block, whose own opacity would change its grade. Each layer fades on its own, so during
a dissolve a credit over a picture lets a little of the picture through; a 22 s render showed no difference at the fades
beyond what two renders of one page already differ by.

The voice, the music bed and the sound effects are HyperFrames `<audio>` elements. The music's level is `gain_db`; its duck is a
volume lane made from the word timings: the gain falls to `duck_under_voice_db` over 0.25 s before each stretch of speech,
holds, and comes back over 0.8 s after it; a pause shorter than 1.05 s keeps it down. Without an alignment the music is not
ducked and the composer warns. `master` runs after the render.

**The rows in HyperFrames Studio.** `data-track-index` only places a clip on a timeline row; it never changes the picture
(stacking is the `z-index`). The rows read from the top as Pictures (`photo-shot`, `video-shot`), Graphics (titles, quotes,
counters, ledgers, charts, maps, clippings, vector graphics), Labels (`credit-line`, `date-stamp`, `lower-third`) and
Finish, then the voice, the music and the sound effects. Each kind gets as few rows as keep its clips apart in time, because
Studio's move and trim refuse two clips at once on a row; the effects share rows the same way when `compose` measured their
files with ffprobe, and an effect it could not measure keeps a row of its own. Every clip carries a label of its shot, kind and words, such as
`lift · Date: April 1945` (the words cut to 48 characters; a picture or clip shows its file name). The table is
`COMPONENT_ROWS` in `src/hyperframes_mcp/motion/compose.py`; a test fails when a block in the registry is missing from it.

## Commands

```
python -m hyperframes_mcp.motion compose SHOTLIST.json --alignment words.json --assets DIR --out PROJECT_DIR
python -m hyperframes_mcp.motion compose SHOTLIST.json --alignment words.json --assets DIR --out PROJECT_DIR --open
python -m hyperframes_mcp.motion compose SHOTLIST.json --alignment words.json --assets DIR --out PROJECT_DIR --profile NAME
python -m hyperframes_mcp.motion layers PROJECT_DIR --from 60 --to 70 --kind photo-shot --json
python -m hyperframes_mcp.motion master EXPORT.mp4 --out VIDEO.mp4
python -m hyperframes_mcp.motion build SHOTLIST.json --alignment words.json --assets DIR --out VIDEO.mp4
python -m hyperframes_mcp.motion build SHOTLIST.json --alignment words.json --assets DIR --check
```

`compose` and `build` take `--alignment` (the word timings), `--assets` (the folder every path in the shot list is inside),
`--out`, `--profile` (default `default`), `--check` and `--node-dir` (the folder whose `node_modules` holds the pinned
HyperFrames; the default is the one `hyperframes-mcp setup` installs, `~/.hyperframes-mcp/data/node`).

- `compose` writes the project (the blocks used, `tokens.css`, GSAP, the fonts, the assets, `index.html`) and lists what it wrote.
  With `--open` it then runs the pinned CLI's `hyperframes open`, which hands the project to the HyperFrames desktop app.
  Exit 1 when the app does not open it, with the CLI's reason and where to get the app; the project stays written. This
  is the assemble-only way: a person edits and renders in the app, and the agent reads what changed with
  `hyperframes catch-up` before its next edit. The app's own Claude Code chat is a new session in the project folder,
  not the session that composed it, so `compose` also writes the project's `VIDEO.md`: which of the server's tools to use
  for what, the layer ids to keep, the profile's spend limits and house rules, what to do when the tools are missing, the
  rule on keys, and that the person renders. The app starts its Claude with the person's own settings only, so a project
  `CLAUDE.md` would never load there. `compose` also writes the project's manifest, `video.project.json`
  (`src/hyperframes_mcp/motion/manifest.py`): the profile (`--profile`, default `default`), the shot list and its sha256,
  the word timings, the assets, the voice, the canvas and the HyperFrames version. The server's tools (`docs/tools.md`)
  read it. `build` composes into a scratch folder that is not kept, so it writes neither file. The file count `compose`
  prints includes `VIDEO.md` and the manifest.
- `layers` prints the table of a composed project's layers: each one's start and end, id, kind, file or text (cut in the
  printed table only; `--json` has it whole), the credit lines that belong to it (a picture's credit, a graphic's source
  line; nothing ties them, so they are read from the times), whether it is `ai` (its record or its credit says AI-generated), `archival` (any other
  picture or clip) or a `graphic` (a block), and the narration spoken over it, widened to whole sentences, from the
  word timings the manifest names (no manifest, no narration column). `--from` and `--to` keep the layers on screen in
  that stretch (seconds), `--kind` (repeatable) keeps those components, and `--json` prints the rows as JSON. The page
  is read as the app left it: a layer is known by its id `l-<shot>-<n>`, never its class, and its place in the stack
  comes from its z-index.
- Putting a picture or clip in a layer is the server's `replace_media` tool, not a command (`docs/tools.md`; the code is
  `replace_media` in `src/hyperframes_mcp/motion/project_edit.py`). It puts a picture or clip file in one layer's place in a
  composed project (a `photo-shot` takes a picture, a `video-shot` a clip, from its start) and sets the credit line on screen
  with it: the `credit` argument, or the credit in the file's record. It changes only those two mounts in `index.html` (the
  variables and the timeline labels) and copies the file into `assets/media/edits/`, never over another file; a file already
  there is used where it is. An outside file without a credit, a file of the wrong kind or a layer that is not a picture or
  clip is refused and nothing changes; a clip shorter than the slot is a warning. More than one credit line on screen with
  the layer needs `credit_layer`.
- Pictures and clips that cost money are the server's tools too: `quote_picture` and `quote_clip` price one and
  `confirm_spend` pays for it. Every paid call is quoted first and confirmed under the limits of
  `src/hyperframes_mcp/config/limits.yaml` (`docs/spend-safety.md`). A project's quotes and spend log are in its `.video-mcp/`
  folder (`quotes.json` and `spend.jsonl`); a central log counts every project. The keys, `OPENAI_API_KEY` and `FAL_KEY`, are
  read by the server itself, from the environment, then `~/.hyperframes-mcp/config/.env`, then the macOS Keychain (service
  `hyperframes-mcp`), so the agent never reads a key. A picture or clip the server made or fetched brings its own credit,
  kept in a record `<file>.json` beside it, which `replace_media` reads.
- `master` sets the loudness of a video rendered elsewhere, such as an export from the desktop app, which is not mastered: `--lufs`
  (default -14) and `--true-peak-db` (default -1), the same `master()` as `build`; it prints the loudness and the sha256.
- `build` composes into a scratch folder, renders with `src/hyperframes_mcp/motion/render.py` (the pinned HyperFrames 0.8.139, strict, offline,
  sandboxed), masters with `src/hyperframes_mcp/motion/master.py`, then prints the render, the loudness of the file written (integrated LUFS,
  true peak, loudness range) and its sha256.
- `--check` on either validates the shot list and prints the resolved timeline (each shot's start and end, each layer's slot, the
  warnings) without writing or rendering. `--alignment` may be left out when no cue is used.
- ffprobe is asked for the voice's length and, for a layer with a `video` variable, the clip's (its video stream): a `trimStart` at or
  past the end of the clip is an error, a slot longer than the clip after `trimStart` a warning that names `hold`. Without ffprobe a
  note says the length is not checked and the plan goes on.
- Exit code 0 on success, 2 when an input is not valid (the messages go to stderr), 1 when a step fails.

`master(video, out, lufs=-14.0, true_peak_db=-1.0)` is two-pass `loudnorm` (one linear gain from the measurement), a limiter half a
decibel under the ceiling, the picture copied and the sound AAC at 48 kHz. It measures the file it wrote with `ebur128`
(`measure(video)`), and if the loudness is more than 0.15 LU off or the peak over the ceiling it runs again with the target moved
by the error, at most three passes. A video with no sound, or a silent one, is refused.

## What was verified, and what was not

Verified (2026-10-05):

- `tests/motion/` (the shot list, timing, compose, master and CLI tests with fakes; `tests/motion/test_shot_list_doc.py` runs the
  example above through the composer).
- A real render with `hyperframes@0.8.101` under the no-network sandbox (`tests/motion/test_composer_integration.py`, opt-in with
  `HYPERFRAMES_MCP_RENDER_INTEGRATION=1`): a 12 s list of an act title, two lower thirds (two mounts of one block, partly overlapping) and a
  ledger, with a dissolve, a music bed and a sound effect. 1920x1080, 24 fps, H.264, 12.00 s as planned; frames at the entrance, the
  hold, mid-dissolve and the ledger's hold show each lower third with its own text, side and timing; two renders have the same sha256;
  the music, a steady tone, is 9 dB lower between the narration's first and last word than before it (measured on the render, with a
  silent voice file); mastered to -14.0 LUFS, at most -1.0 dBTP.
- Two mounts of every block in one shot (all seven blocks that existed then, overlapping) render in strict mode, which fails when a block's timeline does not
  register; only the lower third was looked at frame by frame. The same composition id on both mounts works; a different host id
  per mount works too; so the composer writes no per-mount copy of a block.
- HyperFrames' own `lint` on a composed project: 0 errors.
- The preflight (2026-10-06, `tests/motion/test_preflight.py`): each block's `minSlot` against the hand-derived formulas, and the
  block's own script (the page script for five blocks, `countPlan` and `schedule` for the counter and the ledger, `checkSlot` for the bar grid) accepts the number
  and refuses a hundredth of a second less. The hidden-layer rules are tested on a shot (`weber`) in which a quote card hid a
  photograph and its credit line, broken and fixed.
- `video-shot` (2026-10-06): the composer's `video` copy and rewrite, the credit rule (read from the registry flag), the clip checks
  and the warnings are tested with stand-ins and with a fake ffprobe (`tests/motion/test_compose.py`, `test_composer_cli.py`), and the
  whole path was rendered for real: `build` of a 9 s `freeze` shot and a 9 s `loop` shot with `trimStart` 2, each with its credit
  line, of one 6 s clip; the barcode of all 432 frames of the render is the expected source frame
  (`docs/motion-library.md`, `video-shot`).
- `split-bar` and `compare-bars` in the preflight (2026-10-06): their `minSlot` against hand-computed values, the page script of each
  accepting the number and refusing a hundredth of a second less, a layer too short refused through `plan` with the number, a label
  with no free place refused through `plan`.

Not verified:

- A real image through `image` (the copy and the rewrite are tested; no composed render of one had been run when this was written), a graded photograph under a
  dissolve, and a real voice under the music duck (the duck was measured with a silent voice, so there is no carve of the voice's own
  frequencies: this is a plain volume duck).
- A person watching the motion; the HyperFrames Studio preview; the render without `sandbox-exec`.
- The `max_simultaneous_moving` limit of the tokens across layers: the composer does not count what moves at once.
- The shortest slot of `map-route` and `newspaper-clipping` (noted as not pre-checked), and the layout errors of any block (rows
  that do not fit, a label too long for its column). The counter and the ledger were run through their own slot functions in Node,
  not as a whole page (the stand-in page has no `style.setProperty`); a hidden layer is judged from slots, not from rendered frames.
