# Motion graphics library

The library of motion-graphics components for HyperFrames videos: blocks that an agent names and fills with
variables but never writes. It holds the tokens, the registry and installer, the linter, the blocks listed under "Components"
and the composer that turns a shot list into a video. The code is `src/hyperframes_mcp/motion/`; its assets (blocks, fonts,
tokens, registry, data, js, textures) are in the package's `src/hyperframes_mcp/assets/motion` folder, which this page writes
as `assets/motion/`.
The shot list the composer reads, its rules, the `compose` and `build` commands: `docs/motion-shot-list.md`. The tools of the
server that edits a composed project: `docs/tools.md`.

## Why: the shot-list rule

Left to itself, an agent writes each video's graphics ad hoc: HTML, CSS and GSAP code per video, different every time, with
hard-coded colours, bounces and CDN links that only a render could catch. The rule of the library:

- the agent writes a **shot list** (JSON) that names components and fills their declared variables;
- a **composer** (`src/hyperframes_mcp/motion/compose.py`) emits the HTML of the video from library components;
- the agent **never writes animation code**.

What a component may do is fixed once, in the contract below, and checked by the linter, so a render is a pure function of the
time and the look is the same in every video. A layer of a shot looks like this (the format is in `docs/motion-shot-list.md`):

```json
{"component": "credit-line", "at": 12.0, "duration": 6.2, "variables": {"text": "Library of Congress, public domain", "align": "left"}}
```

## Layout

```
assets/motion/         (src/hyperframes_mcp/assets/motion)
  tokens.json          the single source of the look (night and paper)
  registry.json        the items: name, type, files, variables, licence, source
  blocks/              one HTML file per block (credit-line.html)
  data/                map-germany.js, map-europe.js, map-world.js (Natural Earth, built by map-data) and DATA.md
  js/                  map-lib.js, the pure helpers of the map blocks (projections, label placement, schedule)
  fonts/               Newsreader, IBM Plex Sans Condensed, Courier Prime, their OFL text, FONTS.md
  textures/            the three finish tiles (CC0), TEXTURES.md, make_tiles.py
  THIRD_PARTY.md       what is copied from elsewhere, the licence, what we changed
src/hyperframes_mcp/motion/   tokens.py, registry.py, lint.py, stage.py, render.py, sheet.py, grade.py, preflight.py, compose.py, __main__.py (the CLI)
tests/motion/          tokens, registry and installer, lint, the reference component, the CLI, library hygiene
```

GSAP is not in this folder or in the package. `hyperframes-mcp setup` installs the pinned GSAP (3.14.2, in
`src/hyperframes_mcp/node/package.json`) from npm into `~/.hyperframes-mcp/data/node`, and an install of a block copies its
`gsap.min.js` into the project as `vendor/gsap.min.js`; a composition never loads it from a CDN (`assets/motion/THIRD_PARTY.md`).

## The component contract

A component is one HTML file under `assets/motion/blocks/` with an entry in `registry.json`. The reference is
`assets/motion/blocks/credit-line.html`. The contract, in the order a reviewer reads a file:

1. **Root.** An element with `data-composition-id`, `data-width="1920"`, `data-height="1080"`, `data-start` and
   `data-duration` (the canvas of the tokens). The id is the registry name.
2. **Variables.** Everything a shot list may set is declared in `data-composition-variables` (a JSON array of
   `{id, type, label, default}`; types `string`, `number`, `color`, `boolean`, `enum`) on the `<html>` element, as HyperFrames
   documents it, or on the root. The registry entry repeats the same list (a test compares them). The first variable of every
   block is `durationSeconds` (a number whose minimum is the fades plus the reading time): the length of the block's slot in the
   video. The timeline fills exactly that length (the first tween starts at 0, the last ends at `durationSeconds`), the root's
   `data-duration` and the registry `duration` equal its default, and a slot too short for the fades and the reading time throws.
3. **Reading variables.** The `readVars()` idiom of the animated scene blocks: the declared defaults first, then what
   `window.__hyperframes.getVariables()` (or `window.__hfVariables`) says. A component has no literal fallback: a missing required
   variable throws, so the render fails instead of drawing a wrong frame.
4. **Tokens only.** Colours, type sizes, fonts, the safe zone, durations and eases come from the installed `tokens.css`
   (below); no colour literal, no px type size under the minimum, no other font. A tint is `color-mix()` of a token.
5. **One paused timeline.** `gsap.timeline({ paused: true })`, built synchronously, registered as
   `window.__timelines["<composition-id>"]`. Tweens are `fromTo` with both ends; `gsap.set` is allowed for the first state. A
   component whose layout depends on font metrics (the fit of a text, the lines of a highlight) may build the timeline once its
   fonts have loaded (`document.fonts.load()`) and registers it then, never before; every check on its variables still throws
   first. `newspaper-clipping` does this.
6. **Eases.** The four token eases plus `power1.inOut`, `power2.out`, `power4.out`, `expo.in` and `expo.inOut`. No overshoot
   (`back`, `elastic`, `bounce`), no `steps`, no `circ`, no `sine`. Linear only for a ticker that carries `data-linear-ok`.
7. **What may be tweened.** `x`, `y`, `xPercent`, `yPercent`, `scale`, `rotation`, `opacity`, `clipPath`, `strokeDashoffset` and
   SVG `attr`. Never layout (`width`, `height`, `top`, `left`, `right`, `bottom`, `font-size`, margins, paddings) and never a filter
   or blur: an animated filter made two renders of the same scene differ in most frames.
8. **No CSS motion.** No `transition`, no `animation`, no `@keyframes`: they run on the wall clock, the timeline is seeked.
9. **No clock, no randomness, no network.** No `Math.random`, `Date.now`, `performance.now`, `new Date()`,
   `requestAnimationFrame`, `setTimeout` or `setInterval`; no `http(s)://` or `//` reference (the namespace URIs of SVG are fine).
   GSAP is `vendor/gsap.min.js` in the project (copied there by `install`).
10. **Text.** Set through `textContent`, never as markup. Text under the minimum size is a lint error; credit and source lines have
    their own floor.
11. **Safe zone.** Everything the viewer reads sits inside the safe margins of the tokens.
12. **The minimum slot is exported.** A block whose shortest slot depends on its text (the reading time of its words, the lines of a
    quote, the rows of a ledger) keeps that arithmetic in functions between a `// logic:begin` line and a `// logic:end` line of its
    script, with no DOM in them, and exports `minSlot(V, tok)` there: `V` are the variables, `tok(name)` is the number of a token
    custom property, and the result is the shortest `durationSeconds` in seconds (it throws the block's own error for variables it
    would refuse). The page's own slot check takes its number from that same code (from `minSlot` itself, or from the timing or plan
    function `minSlot` wraps, which also gives the page its tween times), and the composer's preflight runs the same text in Node
    ("The slot minimum comes from the block", below). A block that cannot have such a function says why in the registry entry's
    `slotNotChecked`; a block whose only slot rule is the static `min` of `durationSeconds` says nothing.
13. **Full frame.** A block that paints an opaque ground over the whole frame sets `coversFrame: true` in its registry entry: the
    preflight reports what is under it in a shot as hidden. A translucent scrim or an opaque element smaller than the frame is not one.
    A block that shows a picture or a clip that comes from outside the library (`photo-shot`, `video-shot`) also sets `needsCredit: true`:
    the composer then refuses a layer of it that has no `credit-line` layer with text on screen in the same shot at the same time, and
    the preflight refuses one whose credit line is hidden. The rule reads the flag, not the block's name.
14. **Not machine-checked yet** (a reviewer's job): at most `max_simultaneous_moving` things moving at once, at most
    `max_flashes_per_second` flashes, the safe zone, and that a hold is long enough to read for a block with no `minSlot` (the
    reference derives its hold from `read_min` and `read_per_word`).

## The slot minimum comes from the block

A shot list used to be checked against the static `min` of each block's `durationSeconds`, which is only the floor: `credit-line`,
`date-stamp`, `lower-third`, `act-title`, `quote-card`, `counter`, `ledger`, `bar-grid`, `split-bar` and `compare-bars` also hold their text for
0.3 s a word and threw at render time (after minutes) when the slot was shorter. `src/hyperframes_mcp/motion/preflight.py` now computes that minimum before anything runs. Two
designs were weighed:

- **Python formulas written from the blocks and tested against them.** No dependency on Node at check time, but the formula exists
  twice and a test only notices a drift on the inputs it tries. The first hand-derived formulas showed what that costs: they
  read the total line of a ledger where the block reads the big figure and its caption, and they assume a quote of three lines or more
  where the block staggers its lines by their number (a one-line quote needs half a second less), so a short quote would have been
  refused a slot the block accepts. The line count would need the font advance table in Python too.
- **The block exports its own function (chosen).** The arithmetic is written once, in the block, and the block's own slot check calls
  it, so the number and the throw cannot differ. Node runs it without a browser, without GSAP and in one process for the whole shot list
  (`tokens.css` plus the custom properties the block's own stylesheet declares, such as the text width of the quote card, give `tok`:
  the same two sources a browser resolves).
  The cost: Node is needed at check time (it is needed to render anyway; without it the plan warns and checks only the static minimum),
  and a block has to keep this arithmetic out of its DOM code. `tests/motion/test_preflight.py` still tests the exported numbers
  against the hand-derived formulas, and checks that each block refuses a slot a hundredth under the number and accepts the number.

The blocks compare the slot with `needed` using a tolerance of a millionth of a second, the preflight the same, so a slot at exactly
the minimum passes in both. `map-route` (its minimum is the camera move, the pin entrances and the projected route, known only once
the map is drawn) and `newspaper-clipping` (its words and highlight are tangled with its DOM code) are not pre-checked: the plan
prints a note for them, never silence.

## Tokens

`assets/motion/tokens.json` is the single source; every section carries a `_sources` note that says where its numbers came from.
`src/hyperframes_mcp/motion/tokens.py` loads it with a closed schema: an unknown or
missing key, a colour that is not `#rrggbb` and a number that is not positive are errors, all reported together with their path.

| Section | Holds |
|---|---|
| `canvas` | 1920 x 1080 at 24 fps |
| `colors` | night `#27211A`, night_raised `#352D24`, paper `#EFE6D2`, paper_aged `#B9AE98`, ink `#1E1A16`, gold `#E6B94D`, gold_dim `#B8923F`, oxblood `#8C2F2A`, verdigris `#2F6F66`, slate `#3F5A73`, loss `#E4806F`, gain `#5FA396`, scrim `#17140F` |
| `fonts` | display Newsreader (headings, quotes), label IBM Plex Sans Condensed (lower thirds, labels), mono Courier Prime (ledger and document text only); weights, files, licence |
| `sizes` | title 96, subtitle 64, label 52, body 56, ledger 48, credit 30 px; minimums: text 48, credit 30 |
| `safe` | left and right 96, top and bottom 54 px |
| `ease` | enter `power3.out`, exit `power2.in`, move `power2.inOut`, emphasis `expo.out` |
| `duration` | enter 0.7, exit 0.5, micro 0.3, cinematic 1.2 to 2.0, group stagger total at most 0.5, word stagger 0.1, lower third hold 4 to 6, read 1.2 plus 0.3 per word, dissolve 0.5 to 1.0 (seconds) |
| `camera` | at most 90 px per second of pan and 0.085 per second of zoom |
| `finish` | grain 0.13, vignette 0.15 |
| `limits` | at most 6 things moving at once, at most 3 flashes per second |
| `contrast` | the minimum ratio (4.5), the text pairs the library draws, the decorative colours |

Gold (`#E6B94D`) is for dates and numbers on night; oxblood is the accent on paper only (on paper_aged it is 3.75:1, so it is not
a pair); the typewriter face is for ledger and document text only. The colour values are those of `tokens.json`; no value was changed to pass contrast: every text pair passes as written.

**Contrast.** `contrast_problems()` in `src/hyperframes_mcp/motion/tokens.py` checks every listed text pair against 4.5:1 (WCAG 2.x). As written:
ink on paper 13.9, paper on night 12.8, gold on night 8.7, oxblood on paper 6.6, ink on paper_aged 7.9, paper_aged on night 7.3, gold_dim
on night 5.5, loss on night 5.8, gain on night 5.4, verdigris on paper 4.7, slate on paper 5.8. A pair whose foreground is listed
as decorative is skipped; `night_raised` and `scrim` are, because they are grounds and backings, never text.

**In CSS and script.** `python -m hyperframes_mcp.motion tokens-css` prints the `tokens.css` an install writes: a `:root` block with
`--color-<name>`, `--font-<role>`, `--size-<name>` (px), `--safe-<side>` (px), `--dur-<name>` (seconds, no unit) and `--ease-<name>`
(a GSAP ease name), with underscores in a name written as hyphens, then the `@font-face` rules. The durations and eases are for the
component's script: it reads them with `getComputedStyle` on its own element and throws when one is empty, which means `tokens.css`
did not load. The camera limits and the finish values are exported the same way, for the script: `--camera-<name>` (the `camera`
section: pixels per second of pan, log scale per second of zoom) and `--finish-<name>` (the `finish` section, 0 to 1); `photo-shot`
reads both.

## Lint

`python -m hyperframes_mcp.motion lint [PATH ...]` (default: all of `assets/motion`) reads the text of each `.html` file; it never runs the page.
Exit code 1 when it finds a problem, 2 on a bad path. `src/hyperframes_mcp/motion/lint.py` has `lint_text`, `lint_file` and `lint_tree`.

| Code | What it finds |
|---|---|
| `NETWORK_URL` | an `http(s)://` address, or a `//` address in `src`, `href`, `@import` or `url()`, outside a comment (SVG namespace URIs are fine) |
| `RANDOM_OR_CLOCK` | `Math.random`, `Date.now`, `performance.now`, `new Date()`, `requestAnimationFrame`, `setTimeout`, `setInterval` |
| `BARE_GSAP` | `gsap.to`, `gsap.from` or `gsap.fromTo` on the global object; `gsap.set` and `gsap.timeline` are allowed |
| `NOT_PAUSED` | a `gsap.timeline()` without `paused: true` |
| `NO_TIMELINE_REGISTRATION` | no `window.__timelines["id"] = tl` anywhere in the scripts |
| `TWEEN_FROM` | `.from(` on a timeline: use `fromTo` |
| `BANNED_EASE` | an ease outside the allowed list (see the contract), including overshoot, steps, circ, sine, and linear without `data-linear-ok` |
| `CSS_TRANSITION` | `transition`, `animation` or `@keyframes` in CSS, inline styles or `style.transition` |
| `LAYOUT_TWEEN` | a `to`, `from` or `fromTo` that animates `width`, `height`, `top`, `left`, `right`, `bottom`, `font-size`, a margin or a padding |
| `FILTER_TWEEN` | a tween of `filter`, `blur` or `backdropFilter` |
| `OFF_TOKEN_COLOR` | a hex, `rgb()` or `hsl()` literal that is not a token value (a hex that equals a token colour is accepted; ids such as `#fade` and `url(#id)` are not colours) |
| `SMALL_TEXT` | a `font-size` or `font` shorthand in px under the minimum; a rule whose selector or class contains `credit` or `source` uses the credit minimum |
| `FONT_NOT_IN_TOKENS` | a `font-family` other than the three token families, their generic fallbacks or a font variable |
| `MISSING_VARIABLES` | no `data-composition-variables` on `<html>` or on the root |
| `MISSING_ROOT_ATTRS` | a root without `data-composition-id`, `data-width` 1920, `data-height` 1080, `data-start` or `data-duration` |
| `ALLOW_WITHOUT_REASON` | a `lint-allow` marker with no reason (it excuses nothing) |

**Opting out.** A line opts out of one code with a trailing `/* lint-allow: CODE reason */` (in CSS or script) or
`<!-- lint-allow: CODE reason -->` (in markup). The reason is mandatory; the marker excuses only that code and only that line.

**Limits of the linter.** It is a text scanner, not a parser: comments are blanked, strings are respected when script comments are
found, and a regular-expression literal that contains a quote can confuse it. An ease or a size that is computed rather than
written as a literal cannot be seen; that is why a component reads its eases and durations from the tokens.

## Installing into a HyperFrames project

`hyperframes add` accepts only an https registry address, so a local folder cannot be used with it; `python -m hyperframes_mcp.motion install`
does the copy itself (no network anywhere), with the same item fields so an item stays publishable.

```
python -m hyperframes_mcp.motion install credit-line --dir PROJECT
python -m hyperframes_mcp.motion install credit-line --dir PROJECT --force
```

An install writes, under the project directory:

- the item's files at their `target` (a block goes to `compositions/<name>.html`), and its `registryDependencies` first;
- once per project, `vendor/gsap.min.js` (copied from the GSAP that `hyperframes-mcp setup` installed; without it the install stops and says to run setup) and `tokens.css` (generated from `tokens.json`);
- the font files (and the OFL text) of every role the component names through a font variable, into `assets/fonts/`;
- `motion.lock.json`, the sha256 of every file written.

Safety: a target may not be absolute, contain `..` or an empty segment, or reach outside the project through a link; every destination
is checked before the first file is written. A file that exists and differs from the library's copy is **kept**, and reported, when
the user changed it (its hash is not the one in the lock) or it was not installed by us; it is overwritten only when it still matches
the lock (the library moved on) or with `--force`.

Mounting a block in the host `index.html`:

```html
<div data-composition-id="credit-line" data-composition-src="compositions/credit-line.html"
     data-start="12" data-duration="6.2" data-track-index="1" data-width="1920" data-height="1080"
     data-variable-values='{"text": "Library of Congress, public domain", "align": "right"}'></div>
```

A mount's `data-duration` is the block's `durationSeconds`; the two are the same number, set once by the composer. A block
whose slot is too short for its fades and the reading time throws, so a mount can never cut a fade out.

### What the pinned HyperFrames does with a block (read in `hyperframes@0.8.101`, and one render)

- A mounted composition's script runs wrapped, with a scoped `document`, `window` and `__hyperframes`, and only the root's
  subtree is in the page: the `<html>` element of the block file is **not**. A script that reads its declarations from the DOM
  finds nothing and the timeline never registers (the render waits 45 seconds and warns `sub_timeline_readiness_timeout`). That
  is how the first version of `credit-line` failed; the idiom above (defaults from the declaration when it is in the page, then
  `getVariables()`) works both mounted and standalone.
- The compiler reads font declarations only from the HTML text it is given, so an external `tokens.css` is invisible to it. Components
  therefore name fonts only through a font variable (the compiler does not fetch a family it cannot see); a literal family name with
  no inline `@font-face` would be looked up at render time (read in the compiler, not tested). `FONT_NOT_IN_TOKENS` keeps literals to the
  token families, and a composer that inlines `tokens.css` declares every family it uses.
- Sibling paths in a block (`tokens.css`, `vendor/gsap.min.js`, `assets/fonts/...`) stay as authored when the file exists at the
  project root, so the paths in the reference component are project-root paths.

## Adding a component

1. **Start.** Copy the closest component, or a catalog block (record it, see below).
2. **Rewrite to the contract.** Tokens only, variables declared, one paused timeline, `fromTo`, token eases, no network.
3. **Lint.** `python -m hyperframes_mcp.motion lint src/hyperframes_mcp/assets/motion/blocks/NAME.html` until it is clean; a `lint-allow` needs a real reason.
4. **Registry entry.** Add the item to `assets/motion/registry.json`: kebab-case `name` equal to the composition id, type, title,
   description, `license`, `files` (path in the library, target in the project, type), `registryDependencies`, `dimensions` and
   `duration` for a block, `variables` (the same list as in the HTML), `source` and `notes`; for a block, also `coversFrame` when it
   paints an opaque ground over the whole frame, `needsCredit` when it shows a picture or a clip from outside the library, and
   `slotNotChecked` (the reason) when its shortest slot cannot be computed without a render. A block whose slot depends on its text exports `minSlot` instead (contract, item 12); `tests/motion/test_preflight.py`
   fails for a block that does none of the three and is not listed there as having only the static minimum.
5. **Sample variables.** The defaults of the variables are the sample; they must produce a good frame without any override.
6. **Tests.** The scan in `tests/motion/test_library.py` already covers every registered item (lints clean, has a licence, is in the
   registry, third-party notes in step); add the checks of what is specific to the component, as `tests/motion/test_credit_line.py`
   does, and run `pytest tests/motion`. A component whose script decides timing or text layout is also run
   for real in node against a stub document and a recording `gsap` (`tests/motion/block_harness.py`, used by the tests of
   `lower-third`, `act-title`, `quote-card`, `split-bar` and `compare-bars`): the slot check, the tween positions, the stagger and
   the line breaks are tested, not only read. For those blocks it also checks the motion rules of a run (`check_calm`).
7. **Look at frames.** `python -m hyperframes_mcp.motion render NAME --sheet --twice` (see "Rendering and review"): the pinned CLI offline, four frames
   on a contact sheet, and a check that two renders are the same bytes; a static check cannot see layout.

## Vendored items

A component copied from an upstream registry (HyperFrames or another) is the starting point only: official registry blocks are demos
with hard-coded text and colours, overshoot eases and bare `gsap.to` calls. Copy it with its licence notice in the header comment,
rewrite it to the contract, and record it three ways: `source` (the https url and the pinned 40-character commit) and `notes` (what
was changed; required when `source` is set) in `registry.json`, and a row in `assets/motion/THIRD_PARTY.md`. A test fails when a
vendored item is missing from that table. GSAP and the fonts are recorded there as well (the GSAP Standard License is not an OSI
licence).

## Components

| Name | What it is | Key variables | Origin |
|---|---|---|---|
| `credit-line` | an archival source credit in a lower corner: a fade in, a hold of at least the reading time, a fade out | `text`, `align` | hand-written, the reference component |
| `ledger` | a paper ledger sheet on the night ground that fills row by row (a clip-path wipe per row, an oxblood total whose rule is drawn with scaleX, an optional big gold figure that lands last) | `title`, `rows` (JSON, at most 8: label, value, unit), `total`, `bigFigure` (value, caption; empty for none), `rowTimes` (seconds in the slot, empty to space evenly) | hand-written; no third-party code |
| `counter` | a count-up to a target in gold: it counts over 1.2 to 2 s with the emphasis ease, lands exactly on the target, then the unit and the label arrive | `from`, `to`, `prefix`, `suffix`, `decimals`, `label`, `thousandsSeparator` | hand-written; the idea follows the HyperFrames registry `count-up`, no code copied |
| `bar-grid` | A pictogram for a large count: one bar glyph per bar (up to 20000), filled in coloured segments from the top left as a wave of cells that light, flash and settle; a legend row per segment whose count rises with the fill; a full segment gets a wash, a frame and one spreading ring | `durationSeconds`, `title`, `total`, `segments` (JSON, at most 5: count, label, tone, at, seconds), `unit`, `note` | hand-written; the idea is the pictogram, no code copied |
| `lower-third` | A name and a role on a paper band with a double rule in ink in a bottom corner; the band wipes in, the rule draws, the text rises 16 px, the exit is a faster fade; the hold is at least `lower_third_hold_min` | `durationSeconds`, `name`, `role`, `side` (left, right) | adapted from the HyperFrames block `lt-accent-underline` (Apache-2.0) |
| `act-title` | A full-screen act title on the night ground: a gold kicker, the title word by word through a clip-path mask, a thin gold rule, an optional subtitle; the hold is at least 2.5 s and the reading time; the exit fades the whole block | `durationSeconds`, `kicker`, `title`, `subtitle` (may be empty) | adapted from the HyperFrames component `titlecard-calm` (Apache-2.0) |
| `quote-card` | A primary-source quote on a paper card over the night ground: display italic in ink under an oxblood opening mark, revealed line by line, then the attribution and the source line; the hold is at least max(1.2 s, 0.3 s a word of the quote) | `durationSeconds`, `quote` (at most 220 characters), `attribution`, `source` | adapted from the pull-quote card of the HyperFrames student kit (MIT) |
| `newspaper-clipping` | A paper cutout with seeded torn edges, a fixed slight tilt and two tape strips on the night ground: source line, dateline, headline, short body, an optional halftone photo and a marker highlight. Only ever a real archival document. | `durationSeconds`, `sourceLabel` (required), `dateline`, `headline`, `body`, `image`, `highlight`, `edgeSeed`, `side` | Studied `torn-paper-rip` and `freeze-frame-dressing` (HyperFrames, Apache-2.0) and two cards of the student kit (MIT), rewritten; see `assets/motion/THIRD_PARTY.md` |
| `photo-shot` | A full-frame archival photograph with one slow camera move between two crops, graded and finished through `data-color-grading`, with an optional scrim | `durationSeconds`, `image`, `from`, `to`, `grade`, `scrim` | Hand-written; no third-party code |
| `video-shot` | A full-frame silent image-to-video clip shown for its slot, cropped to cover the frame, graded and finished through `data-color-grading` like a photograph, with an optional scrim; the last frame is held or the clip plays again when the slot outlasts it | `durationSeconds`, `video`, `trimStart`, `grade`, `scrim`, `hold` | Hand-written; the wrapper, scrim and grading follow `photo-shot` |
| `date-stamp` | A short gold rule that draws and a date revealed left to right on a dark plate, in a safe-zone corner | `durationSeconds`, `text`, `position` | Hand-written; no third-party code |
| `graphic-stamp` | A vector graphic over the picture, from an SVG file of the project: inline SVG that stays sharp, in a palette colour for what paints with `currentColor`, in a corner of the safe zone or the centre, small, medium or large; it enters as a stamp (scales down from 1.3 and turns from -6 degrees with the emphasis ease), a draw-on (each stroke draws itself, one micro step apart at most; a shape that is only filled is wiped in from the left) or a fade, all over the enter time, then holds and fades out | `durationSeconds`, `svg`, `position`, `size`, `color`, `animation` | Hand-written, no code copied; the SVGs come from `src/hyperframes_mcp/motion/vectors.py` and the server's `search_icons`, `save_vector` and `vectorize` tools (`docs/tools.md`) |
| `finish` | A full-frame transparent overlay of paper dust and fine scratches, three tiles stepped at 12 a second | `durationSeconds`, `dustOpacity` | Hand-written; textures derived from two ambientCG CC0 maps |
| `map-route` | A map shot: land and sea, a camera move, then pins with one pulse ring and a label each, a route drawn on, a date in a corner and a caption. Overlays move only while the camera is held. | `durationSeconds`, `region` (germany, europe, world), `pins`, `route`, `yearLabel`, `caption`, `cameraFrom`, `cameraTo`, `routeStyle`, `borders` | ours; no third-party code |
| `map-kit` | What the map blocks load: `data/map-germany.js`, `map-europe.js`, `map-world.js` and `js/map-lib.js`. A component, installed with `map-route`. | none | data: Natural Earth, public domain, built by `map-data` (`assets/motion/data/DATA.md`); library: ours |
| `split-bar` | One whole bar split into labelled parts on one scale: a faint track, parts that grow into it from the left in a tone each, a pole and a two-line label per part above the bar (below it when two labels would meet), and brackets under the bar for ranges on the same scale. What the parts leave over stays the faint track, unlabelled. | `durationSeconds`, `wholeLabel`, `wholeValue`, `parts` (JSON), `whole`, `markers` (JSON, may be `[]`), `note` | hand-written, no code copied |
| `compare-bars` | Two to four horizontal bars on one scale, each grown from the left to its value in a tone, with a two-line label inside it when it is long and to its right when it is short; a bar of four percent of the largest is still a clearly outlined sliver. | `durationSeconds`, `title`, `bars` (JSON), `scaleMax`, `note` | hand-written, no code copied |

Every block also takes `durationSeconds` first. Two things about `ledger` and `counter` that the contract does not say:

- **A displayed number is a render of the timeline, not an event.** `counter` tweens a plain object and shows its value from a GSAP
  modifier on that tween, not from `onUpdate` or `tl.call`: a callback is skipped by a seek that suppresses callbacks (a quiet seek
  just before the real one, a scrub), and a seek that jumps past the end of the count would leave the text of the frame before, a few
  units short of the target. A modifier runs on every render of the property, so the text is exact at every frame, forward or backward.
- **Text is sized by what the browser draws, not by the default instance of a variable font.** The advances in `fonts/FONTS.md` are of the
  default instance (Newsreader digits 0.55 em); at weight 500 and the optical size the browser picks for a heading the digits are 0.62 em.
  The two blocks fit a figure to its width with the measured value plus a margin, and a test measures the font at the drawn axes.

Notes that hold for `lower-third`, `act-title` and `quote-card`:

- **Staggers.** Lines come half the group stagger total (0.25 s) apart and the words of a title one word stagger (0.1 s) apart; a
  group of more than three lines or six words is squeezed so that all of it starts within the 0.5 s group total. A staggered
  group counts as one thing in motion.
- **The quote's lines are broken in the script**, not by the browser: the script cannot see the final layout (fonts load after
  it runs), so it measures each word with the advance widths of Newsreader Italic (a table in the file, checked against the
  font by `tests/motion/test_quote_card.py`), picks the largest of the subtitle, body and minimum text sizes at which the quote
  fits a 420 px tall block (five, six or seven lines), and reveals each line through its own clip-path. A quote that cannot fit at
  the minimum text size, a word wider than the card, and a text over its `maxLength` all throw.
- **A throw is a failed render, but a quiet one.** With the pinned HyperFrames the timeline never registers, the render waits
  45 seconds and finishes with the warning `sub_timeline_readiness_timeout` and a video without the graphic (seen with a lower
  third in a 5 s slot). Whatever calls the render has to treat that warning as an error.

### graphic-stamp

The SVG is a file, so the block fetches it by its project path (`assets/media/vectors/<name>.svg`, the folder `save_vector`
writes; a shot list names it in the assets folder as `vectors/<name>.svg` and the composer copies it, as it does an `image`)
and registers its timeline once the file is in, the async-setup contract that `newspaper-clipping` uses for its fonts. Every
check on the variables still throws first. What the page is given is read again: only the drawing elements stay, with no
script, style, event attribute or reference to anything but an id of its own, and every id gets a number of its own per
mount, so two graphics on screen never share a gradient or a clip.

- **Colour.** The art carries a palette token as its CSS `color`; an SVG paints with `currentColor` to take it. A palette colour
  written in the SVG is kept as it is.
- **Where.** A square of 160, 280 or 440 px against one corner of the safe zone (the picture is aligned to that corner inside
  its square, so a corner graphic touches the margins) or in the middle. Top left is also the date stamp's corner and bottom
  left the credit line's and the lower third's; `add_graphic` moves no other layer.
- **Time.** Every entrance ends at the enter time, so the shortest slot is the same for all three: enter, the shortest reading
  time (`read_min`) and exit, 2.4 s, which is the static `min` of `durationSeconds` (the block has no `minSlot`). A shorter
  slot throws. A draw-on gives each stroke `pathLength` 1 and a dash offset from just over 1 to 0, so nothing shows before
  its turn. There is one tween per target: the pinned HyperFrames' lint warns when it finds several `fromTo` calls on one
  selector, which three entrances written one after another would be.
- **Not checked by a machine** (as for the other blocks): at most six things moving at once. A stamp is one thing; a draw-on of
  an icon with many strokes is a staggered group within one micro step.

### newspaper-clipping

- **Provenance.** The clipping is only ever a real archival document, so `sourceLabel` (for example the archive, the repository and
  the date of the page) is required: the block throws when it is missing or empty and draws it as the first line of the clipping.
  No masthead is ever invented; the defaults are neutral words marked SAMPLE ("Sample newspaper"), so a default render cannot be
  mistaken for a real clipping.
- **Look.** Paper with a lighter paper-core band along the torn edge (a seeded generator: the same `edgeSeed` always gives the same
  edge, a different one a different tear), a fixed tilt of 1.2 degrees, two tape strips in a paper tint, no shadow. The dateline is
  oxblood on paper, the headline Newsreader bold (the large title size up to 36 characters, the subtitle size above), the body label
  type at the minimum text size. The clipping is cut to its text (760 to 1240 px wide); with a photo it is 1560 px wide.
- **Motion.** The paper rises 20 px and settles the last degree of its tilt over the enter time, the tape is pressed on, one slow
  push-in of 3 percent over the hold (a single tween), the marker highlight draws after the headline can be read, a fast exit that
  ends with the slot. The highlight is the first match of `highlight` in the headline, else in the body, widened to whole words;
  each word has its own marker box (`scaleX`, multiply blend), so a highlight that wraps to a second line still draws left to right,
  line by line. A `highlight` that is in neither throws.
- **Photo.** `image` is a path inside the project (an address, an absolute path or `..` throws). The photo is shown as a halftone by
  the per-element grading of the pinned HyperFrames (`data-color-grading` on the `<img>`, the `monoScreen` effect: pure black dots on
  white; the four-colour `halftone` effect leaves coloured specks and was not used). The block lightens the black to the ink colour
  and multiplies the photo onto the paper. The image itself is never tweened: the whole clipping fades, which is a wrapper. An image
  that cannot be loaded fails the render.
- **Capacity.** The text must fit the safe zone (reduced by the reach of the tape, the tilt and the push-in, so everything stays inside
  even for a clipping as tall as the stage), else the block throws and registers nothing. Measured in a render: about 45 words under a
  large two-line headline, about 60 or more under a longer headline in the subtitle size, about 40 beside a photo. The hold is at
  least the larger of the minimum reading time and 0.3 s per word of the headline and body; a shorter slot throws.
- **Built after the fonts load.** Layout (the fit check, the headline size) depends on the fonts, which are not loaded when the script
  first runs, so the timeline is built after the faces are in and registered then (the async-setup contract of HyperFrames). A throw
  in the block (a missing source label, text that does not fit) leaves the timeline unregistered: a render waits 45 seconds and warns
  `sub_timeline_readiness_timeout`, the same as a throw before the build.
- **Verified (2026-10-05).** Installed into a scratch project and rendered with `hyperframes@0.8.101` under the no-network sandbox:
  the defaults, a three-line headline with a highlight that wraps to a second line, a short headline with a highlight, and a
  halftone photo (a synthetic picture; left, centre and right). The frames at the start, the end of the entrance, the middle and the
  start of the exit were looked at. Everything drawn stays inside the safe zone even for the fullest clipping (photo, about 50 words,
  right side, at the end of the push-in: 3 px to spare). Two renders of each of the four scenes gave the same sha256; in one of nine
  extra renders of the photo scene, 23 pixels on the torn edge differed by at most 4 levels (rasterisation while other jobs used
  the GPU; not looked into further). `hyperframes lint` and `validate` give 0 errors. A missing source label, a highlight that is not in
  the text, a slot that is too short, a text that does not fit, an image that does not load and an image address each fail in
  `validate` with their own message.
  Not verified: a real archival photograph, the HyperFrames Studio preview, a person looking at the motion (frames only).

### photo-shot

- **Crops.** `from` and `to` are the camera frame at the start and the end, `[x, y, width, height]` as fractions of the image, as JSON
  text or a plain array. The crop keeps 16:9 on screen, so its height is `width x (9/16) x (image width / image height)`; the layer takes
  its shape from `from`, so a crop of the wrong aspect stretches the photograph. `from` and `to` must have the same aspect (within 1%) and
  lie inside the image, or the render fails.
- **The clamp.** Whatever crops are asked for, the camera never pans faster than `camera.max_pan_px_per_s` or zooms faster than
  `camera.max_zoom_per_s` of the tokens. The move is eased with `power1.inOut`, which at its fastest is twice the average speed, so the
  limits hold at that moment and a 6 s shot pans at most 45 px a second on average. The clamp plays the real tween (960 samples) and, when it
  is too fast, pulls the end crop back along its own direction (centre in a straight line, width geometrically) with a bisection; it ends half
  a percent under the limit. The test runs 120 random crop pairs against the pinned GSAP in node and measures the speeds.
- **No fade.** The composer cross-fades shots by fading the shots' layer mounts; the graded `<img>` is never given an opacity tween (the grading shader
  starts a tween of its opacity from black).
- **Grade and finish.** `grade` is JSON of the shader's adjustments (`exposure` in stops, `contrast`, `temperature`, ... in -1 to 1; an unknown
  key or a value out of range fails the render). The library finish (vignette and grain from `tokens.finish`) is always applied; the shape
  of the grain and the vignette (midpoint, feather, size, roughness) is written next to the code in the block. `src/hyperframes_mcp/motion/grade.py`
  gives the `exposure` and `contrast` that put a photograph's median luma in the band 0.32 to 0.50 (`grade_for(path)`, with
  `median_luma(path)`); the shader works in display values with no gamma, so both together are one straight line (the module says
  how the slope is chosen).
- **Findings from the real render** (`hyperframes@0.8.101`). (1) The grading shader draws into a canvas of the layer's own pixel size and
  the camera scales that canvas, so a layer sized for scale 1 went soft when the camera zoomed in (the variance of the Laplacian of one detail
  at the end of a 1.25x push-in was 160, against 634 for a direct Lanczos crop of the same photograph); the layer is therefore made as many
  times larger as the camera zooms in, at most three, and the scale tween shrinks by the same factor (the measure became 246; the rest is the
  video encode, the grain and the grade). No side of the layer passes 8192 px: a crop a tenth of the photograph's width would otherwise ask for
  a canvas 19200 px wide, so the factor can fall below 1 and the canvas is shown a little enlarged. (2) The shader seeds its grain from the image's `id`, or from the full URL when there is none, and the
  render's local port is in that URL: two renders of one shot differed in every frame until the block gave the image an `id` derived from
  its path. (3) A photograph is read from the project when the block runs (the render waits for it); none is stored in the library, and the
  default `image` (`assets/img/sample-photo.jpg`, a 16:9 picture) is a path a sample project supplies.
- **Scrim.** `none`, `left` or `bottom`: a gradient of the scrim token, static, so it fades with the shot.

### video-shot

A full-frame silent clip (an image-to-video MP4 of a few seconds) shown for its slot. There is no camera move, no fade and no sound:
the clip is the motion, and the composer cross-fades shots by fading the shots' layer mounts, as for `photo-shot`.

- **Variables.** `video` is the clip's path in the project: the composer copies the file from the assets folder to
  `assets/media/<same path>` and rewrites the variable, as for `image`. `trimStart` is the second of the clip where playback starts
  (0 to 600). `hold` says what happens when the slot is longer than the clip after `trimStart`: `freeze` (the default) keeps the last
  frame, `loop` plays the clip again from `trimStart` (the part that repeats is `trimStart` to the end of the clip, not the clip from 0).
  `grade` and `scrim` are `photo-shot`'s variables. `durationSeconds` is the slot (1.2 to 60 s). The clip is always drawn to cover the
  frame (`object-fit: cover`): a 4:3 or a square clip fills the 16:9 frame and loses its top and bottom, there is no letterbox variable.
  The default `video` (`assets/video/sample-clip.mp4`) is a path a sample project supplies; no clip is stored in the library.
- **Slot and clip.** The composer asks ffprobe for the clip's video stream length (the sound of a container that runs on is not
  counted, the renderer plays the video) and, in `build --check`, `compose` and `build`: refuses a `trimStart` at or past the end of the
  clip and a file that is not a readable video; warns when the slot is longer than what is left of the clip by more than a frame (1/24 s),
  naming `hold`: "the last frame stays on screen for the last 3 s", or "plays again from trimStart, 3 times in all (the last one is cut off
  after 1 s)". Without ffprobe the clips are not checked and a note says so.
- **Credit.** The registry entry says `needsCredit`, so a layer of this block needs a `credit-line` layer with text on screen with it in
  the same shot, as `photo-shot` does (an AI clip carries "AI-generated illustration"). It also says `coversFrame`: the preflight reports a
  layer under the clip as hidden.
- **Grade and finish.** `data-color-grading` works on a `<video>` in the pinned renderer: the block puts the same JSON on the element that
  `photo-shot` puts on its image (the library finish, vignette and grain from `tokens.finish`, with `grade` on top), so no CSS filter is
  used. A test compares the two attributes for one grade. Measured on a real render (flat colour bars of a test clip): grain (a luma
  deviation of about 0.5 where the clip had none), a vignette (the top edge 6% darker against the middle of the same bar), the grade
  moving the colours, the bottom scrim at 56% of the clip's brightness.
- **How HyperFrames wants a `<video>` in a mounted block** (read in `hyperframes@0.8.101` and, for each point, rendered):
  1. The renderer decodes the video itself: it extracts the frames with ffmpeg (JPEG, at the composition's 24 fps, whatever the clip's
     own rate: a 30 and a 25 fps clip were rendered) and draws the frame of each output time. The page never plays, pauses or seeks the
     element and the block never does either, so a render is a pure function of the time.
  2. A `<video>` whose `src` is set only by the script is found by the renderer but placed by its local `data-start`, with no offset for
     its mount: the second mount of the block drew nothing in its slot. An element with a `src` in the markup is collected first and moved
     to its mount's start and window, and what the script then sets (the `src`, `data-media-start`, `loop`, the end) is applied to it.
     So the markup carries a `src` that is never a file, an empty data URL (`data:video/mp4;base64,`), and the script replaces it from
     `video`; if the script threw before that, the strict render fails with the script's error instead of drawing a wrong frame.
     A path that does not exist did the same in the render but HyperFrames' own `lint` calls it an error (`missing_local_asset`); the
     empty data URL leaves only its warning `media_runtime_src_mutation` (the script writes the `src`), which is this design.
  3. A `data-duration` written in the markup caps the clip: rendered with 6 there, a clip vanished 6 s into a 9 s slot. The block leaves
     it out and its script sets `data-duration` to the slot, which is also the mount's window.
  4. Two mounts of the block both carry `id="video-shot-clip"` in the markup; the renderer gives each its own render id
     (`video-shot-clip`, `video-shot-clip__hf2`), so no id is made from the path as in `photo-shot`. Two builds of one shot list gave
     the same sha256.
  5. Silence: the element is `muted`, has `data-volume="0"` and no `data-has-audio`, and the composer never writes an `<audio>` for a
     clip. A clip with a 440 Hz tone was rendered under a 220 Hz voice: the sound of the render has one peak, the voice's, and at 440 Hz
     it is 109 dB below it.
  6. Freeze needs no still frame: the renderer holds the last frame of a video that has no `loop` for the rest of its window (it
     extracts the final frame), and the window is the slot. Loop is the renderer's own `loop` attribute, which repeats from
     `data-media-start`, which is `trimStart`.
- **Verified (2026-10-06).** `hyperframes@0.8.101`, offline sandbox, a 6 s 1920x1080 24 fps clip with a barcode of its frame number
  (two mounts in one video: a 9 s slot with `freeze`, a 9 s slot with `trimStart` 2 and `loop`, each with its credit line). The barcode of
  all 432 frames of the render was read: every frame is the expected source frame (source frames 0 to 143, then 143 held for 3 s; then
  48 to 143, 48 to 143 again, and 48 to 71). A 4:3 and a square clip fill the frame, and a dissolve between two clip shots fades both
  wrappers. The first render of 18 s took 22 s with the pinned two workers (1.2 s of render per second of video, nearly all of it the
  frame capture), the largest process 0.8 GB, the extracted frames 75 KB each (18 MB for 10 s). Two builds had the
  same sha256. HyperFrames' own `lint` on the composed project: 0 errors and one warning (`media_runtime_src_mutation`, see point 2).
  `HYPERFRAMES_MCP_RENDER_INTEGRATION=1` runs the barcode check as a test (`tests/motion/test_composer_integration.py`).
- **Not verified.** A real image-to-video clip (the clips were test patterns); a variable frame rate clip, HEVC, or a rate other than the
  24, 25 and 30 fps tried; a clip over 6 s; the HyperFrames Studio preview (there the markup's `src` is a missing file until the script sets the real
  one); a person looking at the motion.

### date-stamp

- The date is set in the display face at title size, in the gold token, with lining tabular figures (`font-variant-numeric`; Newsreader has
  `tnum`, and its default figures are lining). The rule draws with `scaleX` from the left; the date is one `clip-path` reveal from the
  left (no per-character spans or timers), one micro step after the rule; the stagger of the group is that one step.
- The plate behind them is the scrim token at 90% in a soft ellipse around the stamp, flat over the text and falling away in small steps so
  that no edge shows; gold stays above 4.5:1 on it even over a white frame (a test). A date of up to 30 characters stays on the flat part.
- The slot must hold the reveal, the reading time (`read_min` plus `read_per_word` per word) and the fade out; a shorter slot fails the
  render. Exit is the token exit (0.5 s, 70% of the enter) with the exit ease.
- `position` is a corner of the safe zone: `bottom-left` (the default, which is also where `credit-line` and `lower-third` sit), `top-left`,
  `top-right` or `bottom-right`. Only the corner changes: the rule, the plate and the left-to-right reveal are the same in each. A stamp that
  covers the subject is moved to another corner, never shortened.

### finish

- Three transparent RGBA tiles, 1920 x 1080, under 400 KB each, of light specks and scratches in the paper colour and a few dark specks in
  the ink colour (about 4% of the frame; `assets/motion/textures/TEXTURES.md` has the sources, licence and recipe). One tile shows at a time,
  stepped 12 times a second with discrete `tl.set` calls in a fixed hash order (never the same tile twice in a row, the same on every render,
  also when the timeline is seeked out of order: a test runs it in node).
- The tiles are their own registry item, `finish-textures` (a component, licence `CC0-1.0`), which `finish` depends on: installing the
  block writes them to `assets/textures/` of the project.
- It adds only what `data-color-grading` cannot: the grading already gives each photograph its vignette and grain, and `finish` does not
  darken, tint or blur anything. `dustOpacity` is the strength of the whole overlay (0.35 by default).
- The overlay fades in and out over the dissolve time at the ends of its slot: mount it once over a whole video or act, not on every shot, or
  the dust would pulse at each cut. The dust is too sparse for a step to count as a flash.

### map-route

Variables: the pins are JSON, `[{"id": "berlin", "label": "Berlin", "lon": 13.405, "lat": 52.52, "labelSide": "right", "kind": "city"}]`,
where `kind` is `city` (a dot and a label), `focus` (a gold dot, a larger label, laid out first), `region` (a spaced label, no dot) or
`via` (a point of the route, nothing drawn); `lon` and `lat` may be left out for a name in the cities table of the region. `route` is
the pin ids in order (`[]` for none). `cameraFrom` and `cameraTo` are `{lon, lat, span}` with `span` the visible width in degrees of
longitude; equal values lock the camera. `routeStyle` is `line`, `rail` or `dots`; `borders` is `none`, `countries` or `regions`.

What it does, in order: the map fades in while the camera moves (power2.inOut; 2.0 s, plus a second per unit of log zoom and per screen
width of pan, at most 4 s; the scale moves in log steps so a zoom feels even, and the easing is baked into 30 sampled poses a second so a
seek is exact); then, with the camera held, the pins arrive one after another (stagger of the group at most 0.5 s: a scale and opacity
`fromTo`, one pulse ring, a label 0.2 s later), the route is drawn on (`strokeDashoffset` with its length measured in the page, 1.2 to
2.0 s), the date and then the caption come in; the hold is the longer of 1.2 s and 0.3 s a word of every text on screen; the whole stage
fades out so the last tween ends at `durationSeconds`. A slot shorter than all that throws and says how long it needs. The
establishing move is deliberately outside the camera limits of the tokens (0.085 zoom a second and 90 px a second of pan, the slow
limits for a photograph): the locator set zooms from 24.7 to 6.7 degrees at about 0.38 a second.

Placement: labels are placed from the label font's advance widths (a table in `js/map-lib.js`, tested against the font files), not from
the DOM, so the layout is the same on every machine. A label takes the side its `labelSide` hint names, then the opposite side, then
above, then below, the first that stays inside the safe zone and clear of every dot, the corner text and the labels already placed
(the focus pin goes first); of those it prefers one the route does not cross. When no side is free the render fails and names the pin.
The date and caption take the top left corner, or the top right when a dot or the route is in the way. At most six pins have a dot or a
label (one pin is one thing moving, and the limit is six); route points of kind `via` are unlimited.

Guards that throw: a pin outside the safe zone at `cameraTo`, a camera that shows ground beyond the data of its region, a name that is
not in the cities table, a route that names an unknown pin, bad JSON (the message names the variable).

Colours are derived with `color-mix()` of tokens: land is night lifted toward paper, the sea is night toward the scrim, rivers are paper
toward slate, the route, the focus dot and the date are gold, labels are paper with a scrim halo (`paint-order: stroke fill`, an edge and
not a shadow). The borders are the modern ones (`assets/motion/data/DATA.md`): use `borders: "none"` for a story set before them.

Three example maps (a locator, a rail route from Berlin to Marburg with its date, trucks from Marburg to Frankfurt) are kept as
variable sets in `tests/motion/test_map_route.py`; the shot's slot has to be at least what the rules above need, and a wide first
view of the locator (Berlin and Frankfurt labelled while the camera moves) is not possible: labels do not appear while the
camera moves.

### bar-grid

A pictogram for a count too large to feel as a number (8,198 gold bars): one small bar glyph per bar, filled in coloured segments from the
top left, row by row, as the narrator speaks, with a legend row per segment whose count rises with the fill. The block is transparent (a
soft dark band behind its content and nothing else), so it sits on the night ground or on a photograph.

| Variable | Holds |
|---|---|
| `title` | what the grid is, at most 70 characters; set at 64 px, on one line or two (the width is measured from the font's advances) |
| `total` | the number of cells, a whole number from 1 to 20000; the whole grid stays on screen, the cells no segment covers as faint empty glyphs |
| `segments` | JSON, 1 to 5 objects `{"count": 8198, "label": "found in the partial count", "tone": "gold", "at": 0.4, "seconds": 3.0}`, filled in order from the top left. `count` is a whole number of at least 1 and the counts add up to at most `total`; `label` at most 38 characters; `tone` is `gold`, `paper`, `ember` or `mute`; `at` is the second of the slot when the fill starts (0 or more, never before the segment above it) and `seconds` how long it takes (more than 0) |
| `unit` | what one cell is, after each count ("bars"); at most 16 characters, may be empty |
| `note` | a small line under the legend; at most 66 characters, may be empty |

The tones are tokens: `gold` is the gold, `paper` the paper, `ember` the `loss` colour (the lifted oxblood, for what was lost or asked) and
`mute` paper_aged. Two shots it was built for, as the shots of a list (a shot writes every variable out, `note` as an empty string when
there is none):

```json
{
  "shots": [
    {
      "id": "partial-count",
      "start": 0,
      "duration": 8,
      "layers": [
        {"component": "bar-grid", "at": 0,
         "variables": {"title": "Marburg: gold bars in the first partial count", "total": 8198,
                       "segments": "[{\"count\":8198,\"label\":\"found in the partial count\",\"tone\":\"gold\",\"at\":0.4,\"seconds\":3.0}]",
                       "unit": "bars", "note": ""}}
      ]
    },
    {
      "id": "for-scale",
      "start": 8,
      "duration": 12,
      "layers": [
        {"component": "bar-grid", "at": 0,
         "variables": {"title": "Gold bars: what was asked, what came back", "total": 8198,
                       "segments": "[{\"count\":3600,\"label\":\"asked of Portugal (about 45 tonnes)\",\"tone\":\"ember\",\"at\":0.6,\"seconds\":3.0},{\"count\":320,\"label\":\"Portugal turned over (about 4 tonnes)\",\"tone\":\"gold\",\"at\":4.6,\"seconds\":1.2}]",
                       "unit": "bars", "note": "A Marburg-sized pile, for scale"}}
      ]
    }
  ]
}
```

- **The grid.** Two canvases, one glyph per cell. The pitch is the largest whole number of pixels, cells three wide for two tall, whose rows
  fit the height the title, legend and note leave (at most 640 px) in the 1728 px between the safe margins; the columns are then trimmed so
  the last row is nearly full. 8,198 cells are 123 columns by 67 rows of 14 by 9 px cells (a grid 1722 by 603 px) with bars of 12 by 7 px;
  20,000 cells are 9 by 6 px cells. A bar is a trapezoid, narrower at the top, drawn row by row so its edge is crisp, with a lighter top
  face. The empty cells are drawn once; the lit cells are redrawn from the time.
- **The fill is a wave.** The k-th cell of a segment starts at `at + (seconds - trail) * k / count`, where the trail is 0.5 s (at most half the
  segment's own time): the cell comes up in its tone, flashes toward paper at the brightest level and settles over the trail in eight steps,
  so every cell has settled by `at + seconds`. The legend count of a segment is the number of its cells started, so the figure and the picture
  agree at every frame and land exactly on `count`. A legend row arrives (fade and 16 px rise) when its segment starts; a single segment's
  count is set large (96 px, the shot's "8,198 bars"), several are 56 px in a column of right-aligned figures, the unit and the label at 48 px.
- **A small segment is seen.** When a segment is full it gets a wash of its tone (16 percent) under its cells, a 2 px frame round their
  outline (90 percent) and one ring that spreads 16 px outward and fades over the entrance time (0.7 s); the wash and the frame stay. The 320
  bars of the second shot (3.9 percent of the grid, two and a half rows) are one gold band with its own frame under the larger ember block.
- **Why a modifier on a ticker.** The canvas is drawn from the time alone: one linear tween (`data-linear-ok`, the only linear ease of the
  block) runs a proxy value that is the time in the slot, and a GSAP modifier on it draws the frame and sets the counts, so a seek that jumps,
  goes back or is the runtime's quiet seek before the real one draws the same frame as playing forward (the reason is the counter's, above).
  The ticker is `addTicker` in the block's logic, which the page and `tests/motion/test_bar_grid_draw.py` both call: the test runs a real GSAP
  timeline of it forward, backward, shuffled and with quiet seeks and compares what was drawn. From `settledAt` (the last segment is full and
  its ring has gone) the picture no longer changes, so it is drawn once into a third canvas and copied, one blit a frame for the hold instead of
  a draw of every cell. No per-cell DOM element or tween exists; 8,198 cells cost about 6 to 8 ms a frame in a render.
- **The slot minimum.** `minSlot` is the end of the segment that is full last (`at + seconds`, the later of two that end together), plus the
  entrance time (0.7 s, the pulse), plus the reading time of that segment's label (`read_min` 1.2 s plus `read_per_word` 0.3 s a word), plus the
  exit (0.5 s). The title, the unit and the note are not read. The cold open needs 0.4 + 3.0 + 0.7 + 1.2 + 0.3 x 5 + 0.5 = 7.3 s; the second
  shot 4.6 + 1.2 + 0.7 + 1.2 + 0.3 x 6 + 0.5 = 10.0 s. `durationSeconds` is 4 to 40 s, 8 by default. The page also throws, naming the
  segment, when one ends after the slot (`segments[1] ends at 7.50 s ..., after the slot`).
- **It throws, naming the variable,** on text in `segments` that is not JSON or not a list of 1 to 5 objects, a `count` that is not a whole
  number of at least 1, counts that add up to more than `total`, a `tone` that is not one of the four, a negative `at`, a `seconds` of 0 or less,
  an `at` before the segment above, a `label` over 38 characters or wider than the legend column (measured with the label font's advances), a
  `total` outside 1 to 20000, a title over 70 characters, a note wider than one line, and a grid that cannot fit the height. The preflight
  checks the slot, not the layout (`minSlot` reads the variables only): a label that is short enough but wider than the legend column, or a
  grid that cannot fit, is refused when the block runs, as the ledger's rows are. The advance tables
  of the title face (Newsreader semibold, optical size 64) and the label face (IBM Plex Sans Condensed Medium) are in the file and
  `tests/motion/test_bar_grid_layout.py` checks them against the font files, and that an estimate is never narrower than the font.
- **Verified (2026-10-06).** `tests/motion/test_bar_grid*.py` (the refusals, the minimum against hand-computed values, the layout over a sweep
  of totals, segments, notes and two-line titles, the outline of every segment shape, the seeks) and the block through the preflight's
  own function. Rendered with `hyperframes@0.8.101` under the offline sandbox for both shots, a worst case (20,000 cells, five segments, a
  two-line title and a note) and 42 cells: 8,198 cells render a clip of 8 s in about 7.8 to 9.6 s (the counter: 6.4 to 6.6 s), the 12 s shot in
  10.6 to 11.9 s (the timings move by a second with the load of the machine); two renders of each shot have the same sha256; the frames at the start, mid fill, end of fill and the hold were looked at. The
  pinned `hyperframes check` reports no runtime error, no layout issue and 34 of 34 contrast checks passing; its lint warns that the file is
  long, like the ledger's. The opt-in `test_render_integration.py` renders the cold open for real.
  Not verified: the block over a photograph (only over the night ground), a device scale factor above 1 (the canvas is pixel for pixel, the
  renderer runs at 1), a person looking at the motion (frames only), the HyperFrames Studio preview.

### split-bar and compare-bars

Two blocks that make a share and a ratio visible with bars. They are transparent over a night ground or a photograph, sit on a soft
backing of the scrim token (88% at its strongest, so every text colour passes 4.5:1 over a white frame), and keep inside the safe zone.
Both set all their text in the label face (IBM Plex Sans Condensed) at 52 px (the label size) for labels and displays, 56 px for the
heading, 48 px for the note, and the only numbers on screen are the texts of the variables: no tick, axis, percentage or total is
computed or printed. A bar or part grows by a left-to-right clip-path wipe with the enter ease, never by a width tween, and has its own
3 px paper outline, so the thinnest one is a clear sliver.

**Tones.** `gold`, `paper`, `ember` and `mute`. `ember` is the `loss` colour (the lifted oxblood, which reads on night; the oxblood
itself does not), `mute` is paper-aged at 38 percent over night. A text inside a bar is ink on gold, paper and ember and paper on mute
(each at 4.5:1 or better, a test); the display line beside a bar or under a flag is printed in the tone's own text colour (gold, paper,
loss, paper-aged).

**The two lines.** A label is two lines: the `label` in paper, then the `display` (what is printed, for example "about two thirds") in
the tone. At most 34 characters for a label and 24 for a display (a bracket's display may be 34, for "about a quarter of the ledger");
longer is an error that names the variable. Widths are not measured in the page: they come from an advance table of the label face
(the widest of its Medium, SemiBold and Bold, per printable ASCII character, thousandths of an em; any other character counts as the
widest glyph of Latin-1 and Latin Extended-A), so the layout is the same on every machine and never narrower than the font. A text
that cannot be placed throws and says which variable to shorten (`tests/motion/test_split_bar_layout.py` checks the table against the
font files; `compare-bars` carries a copy, and a test ties the two). Both blocks keep this and the rest of their arithmetic between
`// logic:begin` and `// logic:end` and export `minSlot(V, tok)`.

**The shortest slot (both).** The latest of these ends, plus the exit (0.5 s): the heading `enter + reading`, the note `enter + reading`,
every part or bar `at + seconds + enter + reading` (it grows, then its label arrives, then it is read), and every bracket
`at + enter + reading`; `enter` is 0.7 s and `reading` is 1.2 s plus 0.3 s a word of the label and display (the library's reading time).
For the last part, that is `at + seconds + entrance + reading + exit`. `minSlot` runs the layout too, so
a text with no place is refused before a render, as a wrong variable is. The declared minimum of `durationSeconds` is 3.3 s, the least any
legal variables need (one part or bar of 0.3 s, one word on each line). Sample slots: the defaults need 10.1 s (`split-bar`, the bracket
arrives last) and 8.5 s (`compare-bars`), against 12 and 10 declared.

#### split-bar

One whole bar, the safe width wide, labelled above it and split into parts.

| Variable | Holds |
|---|---|
| `wholeLabel` | what the bar is, at most 34 characters, above its left end (for example "The Bundesbank's gold ledger") |
| `wholeValue` | its size as printed, at most 24, above its right end, in gold ("about 791 tonnes"); with the label it must fit one line |
| `parts` | JSON, 1 to 6: `{"label", "display", "value", "tone", "at", "seconds"}`. `value` is the part's size on the scale of `whole`; the parts add up to at most `whole`; each is at least 1 percent of it. `at` is the second of the slot when it starts to grow, `seconds` how long it grows (at least 0.3 s) |
| `whole` | the scale: a number over 0 |
| `markers` | JSON, 0 to 3 (`[]` for none): `{"label", "display", "from", "to", "at"}`, a range `from` to `to` on the same scale of `whole` |
| `note` | one short line under everything, at most 70 characters and one line wide, or empty |

- **Parts are placed in list order from the left**: the first starts at 0, each next where the one before ended. What is left of `whole`
  stays the faint track, unlabelled. Parts grow in order: `at` may not decrease, but a part may start before the one before has finished
  growing. A marker can sit anywhere on the scale, inside a part, across two, or over the remainder; to say "this pile sat inside that
  part", put that part first in the list (the example below puts "found in Germany" first so that 0 to 210 lies inside it).
- **Labels.** Each part has a flag: a 4 px pole in its tone from the bar's edge and its two-line label beside the pole. It goes above the
  bar from the part's left edge; when that would meet the label before it, below the bar; then along the part's top or bottom edge,
  clear of the labels there; then, for a part at the right end of the bar, with the pole at its right edge and the text to the left of it.
  A part of 4 percent (about 70 px) is the case this is made for. If none of the six places is free (a part of 1 percent between two
  long labels), the block throws and names the part. The flag arrives, with its pole drawn out of the bar, when the part has grown.
- **Brackets.** A marker is a bracket under the bar from `from` to `to`, drawn from its left end, with its label under it, left-aligned
  to the bracket or, when that runs past the bar, right-aligned. Brackets own the lane under the bar: they may not overlap one another and a
  label below the bar keeps clear of them.
- **Height.** The lanes are as tall as what uses them; the graphic is centred in the safe zone, and at most 972 px tall, else it throws.

An example (a Bundesbank ledger; two parts in the order that puts the Marburg range inside "found in Germany"):

```json
{"wholeLabel": "The Bundesbank's gold ledger", "wholeValue": "about 791 tonnes", "whole": 791, "note": "",
 "parts": "[{\"label\":\"found in Germany\",\"display\":\"about a third\",\"value\":236,\"tone\":\"gold\",\"at\":1,\"seconds\":1.2},{\"label\":\"went abroad\",\"display\":\"about two thirds\",\"value\":503,\"tone\":\"ember\",\"at\":2.6,\"seconds\":1.6}]",
 "markers": "[{\"label\":\"the Marburg pile\",\"display\":\"about a quarter of the ledger\",\"from\":0,\"to\":210,\"at\":5}]"}
```

The 52 tonnes left over (791 less 236 and 503) are the faint track at the right end. It needs a slot of 10.1 s: the bracket arrives at
5.0 s and is read until 9.6 s.

#### compare-bars

Two to four horizontal bars, one above the other, each as long as its share of the scale from the left end of the safe width.

| Variable | Holds |
|---|---|
| `title` | what the bars measure, at most 40 characters, one line, above them |
| `bars` | JSON, 2 to 4, top to bottom: `{"label", "display", "value", "tone", "at", "seconds"}`. `value` is the bar's length on the scale of `scaleMax`, at least 1 percent of it and at most all of it |
| `scaleMax` | the scale: the value of a bar the full width, a number over 0 and at least the largest value |
| `note` | one short line under the bars, at most 70 characters, or empty |

- **Text.** A bar's label sits inside it (two lines, 28 px from its left end) when it fits with that margin on both sides; otherwise to the
  right of the bar, 28 px from its end. A text that fits neither way throws and names the bar. Bars may grow together (`at` may be equal
  or in any order); a bar's text arrives when it has grown.
- **The sliver.** A bar of 4 percent of the largest is 68 px wide by 140 px tall with its 3 px outline, in its tone, and its label and
  display to its right. A bar is as tall as its two lines of text plus 16 px (140 px at the label size of 52), so a larger label size
  grows the bars and never overflows them.
- **Height.** Four bars, the title and a note fit the safe zone (a test); the graphic is centred.

Examples (both rendered on 2026-10-06):

```json
{"title": "Gold, in tonnes", "scaleMax": 20000, "note": "",
 "bars": "[{\"label\":\"America's gold, 1945\",\"display\":\"about 20,000 tonnes\",\"value\":20000,\"tone\":\"paper\",\"at\":1,\"seconds\":1.4},{\"label\":\"The whole Bundesbank ledger\",\"display\":\"about 791 tonnes\",\"value\":791,\"tone\":\"gold\",\"at\":2.8,\"seconds\":1.2}]"}
```

```json
{"title": "Gold, in tonnes", "scaleMax": 400, "note": "",
 "bars": "[{\"label\":\"Swiss banks took in\",\"display\":\"about 395 tonnes\",\"value\":395,\"tone\":\"ember\",\"at\":1,\"seconds\":1.4},{\"label\":\"Marburg pile, Bundesbank's figure\",\"display\":\"about 210 tonnes\",\"value\":210,\"tone\":\"gold\",\"at\":2.8,\"seconds\":1.2}]"}
```

In the first, the Bundesbank's bar is 68 px of 1728 (3.96 percent) and its text sits beside it; in the second, 395 of 400 is 1706 px and
210 of 400 is 907 px, and both texts sit inside their bars.

**Verified (2026-10-06).** The unit tests (`tests/motion/test_split_bar.py`, `test_split_bar_layout.py`, `test_compare_bars.py`, the two
blocks in `test_preflight.py`): validation and every refusal, the layout, the shortest slot against hand-computed values, the page run
in Node against a stub document (a slot at the minimum is accepted, one hundredth under it is refused), lint. Real renders with
`hyperframes@0.8.101` of both blocks' examples above, a part of 4 percent, and the crowded cases (five parts, three brackets and a note;
four bars and a note), frames at the start, mid-growth, the end of growth and the final hold looked at. Not verified: a person watching
the motion; the blocks over a photograph (only over the night ground, so the backing is judged by contrast arithmetic only); text with
characters outside printable ASCII and the typographic marks (they count as the widest glyph, never rendered here).

### Verified, and not

Verified (2026-10-05) for these three: the unit tests, including each block's own script run in node against the pinned GSAP; each block
installed into a scratch project and rendered with `hyperframes@0.8.101` under the offline sandbox, frames looked at (start, the end of the
fade in, the middle, the start of the fade out), two renders byte-identical (the same sha256) for each; HyperFrames' own `lint` gives 0 errors
and `validate` no console error (the `date-stamp` project: 5 text elements pass WCAG AA).

Two mounts of the same block in one video work in `hyperframes@0.8.101`: each mount keeps its own timeline, also when they overlap in
time (checked frame by frame for two `lower-third` mounts; the composer mounts every block that way; `video-shot` was
checked frame by frame in its own section above). Not verified: a `photo-shot` image whose aspect does not match its crops (it stretches, nothing checks it); an image smaller
than the layer the camera needs (it is upscaled); a person looking at the motion (only stills were looked at).

## Rendering and review

A static check cannot see layout, spacing or the moment a fade ends, so a component is reviewed in pixels. One command does it:

```
python -m hyperframes_mcp.motion render credit-line
python -m hyperframes_mcp.motion render credit-line --vars '{"align": "right", "durationSeconds": 9}' --sheet --twice
python -m hyperframes_mcp.motion render credit-line --node-dir /path/to/node-dir
```

| Flag | Does |
|---|---|
| `--vars JSON` | a JSON object with the block's declared variables; the default is the block's own defaults (an undeclared name is an error) |
| `--out FILE` | where the MP4 goes; default `output/motion/NAME.mp4` in the current folder |
| `--sheet` | also extract four frames into `NAME.frames/` and write `NAME.sheet.png` beside the MP4 |
| `--twice` | render a second time (to scratch) and compare the two sha256 |
| `--node-dir DIR` | the folder whose `node_modules` holds the pinned HyperFrames; default `~/.hyperframes-mcp/data/node`, which `hyperframes-mcp setup` fills (`HYPERFRAMES_MCP_NODE_DIR` or `HYPERFRAMES_MCP_HOME` move it) |

Without that install the command says "HyperFrames is not installed: run `hyperframes-mcp setup`". Exit code 0 on success, 1 when the render fails or `--twice` finds two different files, 2 on a bad argument (an unknown block, a
`--vars` that is not a JSON object, an undeclared variable, a duration that is not positive).

**The steps** (each is a module with a function you can call from a test or a script):

1. `src/hyperframes_mcp/motion/stage.py`, `stage_block(name, variables, project_dir, *, start=0.0)`: installs the block with `registry.install_item` (GSAP,
   `tokens.css` and the fonts come with it), writes `index.html` (1920x1080 at 24 fps from the tokens; the root's duration is the block's
   `durationSeconds`, from the variables or the item's default, plus `start`; the block mounted by `data-composition-src` with its
   variables as one JSON-escaped `data-variable-values`), a night ground under the block (a block alone is transparent) and `meta.json`,
   which the HyperFrames CLI needs to take the folder for a project. It returns the lengths the next steps use.
2. `src/hyperframes_mcp/motion/render.py`, `render_project(project_dir, output, *, timeout=None, node_dir=NODE_DIR, settings=None)`: runs the
   pinned CLI (0.8.139) the hardened way and returns the path, the measurements and the sha256 of the file. The hardening is
   written once: the scrubbed environment, the throwaway HOME, telemetry off, the macOS `sandbox-exec` that denies the network, the
   process-group kill on a timeout and the probing of the output live in `src/hyperframes_mcp/hfcli/cli.py`. Limits (workers, the
   timeout factor and floor, the size cap, `network_isolation`, node and browser overrides) are the `animated_scene` section of
   `visuals_pipeline.yaml` (bundled in `src/hyperframes_mcp/config/`; a copy in `~/.hyperframes-mcp/config/` replaces it), one
   place for every HyperFrames render.
3. `src/hyperframes_mcp/motion/sheet.py`: `extract_frames` (ffmpeg: `$FFMPEG_BIN`, else the Homebrew ffmpeg-full when it is installed, else
   `ffmpeg` on PATH; only a plain local video file is opened) and `build_sheet` (Pillow: a grid, the label under each frame in the library's label face, the token colours);
   `review_sheet` does both for the four review moments.

**What a render must be to be accepted.** H.264, 1920x1080, 24 frames per second, a length within half a second of the project's, and at
most the size cap. A clip that is not goes unwritten and the error says why. The CLI itself runs strict (its best-effort mode switched off): by default
HyperFrames exits 0 and writes a clip even when a block's script threw. The case that found this: `credit-line` with a slot shorter than its
fades and reading time throws by design (contract item 2), HyperFrames waited 45 seconds, warned, and wrote a clean-looking clip of empty
frames. Strict, it exits 1, and the error carries the page's own message: "renderer exited with status 1: [Compiler] Composition script
failed credit-line Error: credit-line: durationSeconds 8 is shorter than the 9.90 s it needs".

**The frames.** With `--sheet`, the sheet shows four moments, from the enter and exit tokens: the start (nothing drawn yet for a block that
fades in), the end of the fade-in, 50 percent, and the start of the fade-out. Read them for: is the thing inside the safe zone, is the
text the size and face the contract says, does it overlap anything, is the hold long enough to read. Mid-fade frames are not on the sheet;
extract one with `extract_frames` when a fade looks wrong.

**Determinism.** `--twice` renders the same project again and compares the bytes. Two renders of `credit-line` are identical; a block that
tweens a filter or blur is not, and the linter forbids those for that reason.

**Tests.** `tests/motion/test_stage.py`, `test_sheet.py`, `test_render.py` and `test_render_cli.py` run with a fake CLI, a fake ffprobe and a
fake ffmpeg (shell scripts, `tests/motion/render_fakes.py`) and need no Chrome. `tests/motion/test_render_integration.py` renders
`credit-line` for real and is skipped unless `HYPERFRAMES_MCP_RENDER_INTEGRATION=1`. It needs `hyperframes-mcp setup` (the pinned HyperFrames and GSAP;
`HYPERFRAMES_MCP_NODE_DIR` points at another install), a chrome-headless-shell (`SCENE_RENDER_BROWSER` or `~/.cache/hyperframes/chrome`)
and ffmpeg-full (`FFMPEG_BIN`, default the Homebrew one), and makes no network call and no paid call.

**Verified** (2026-10-05): `credit-line` with its defaults rendered in about 3.5 seconds for its 6.2 second slot, 1920x1080 at 24 fps
H.264, the clip about 75 KB, under the sandbox; two renders had the same sha256; the four frames show the empty night ground at 0, the
credit in the lower left corner of the safe zone from the end of the fade-in to the start of the fade-out; with `align` right and a
12 second slot the long credit sits at the right margin on one line. The failure case above was run for real (both integration tests pass in about a minute, nearly all of it the 45 second wait of the throwing block).

**Not verified.** Any block but `credit-line`; the render without the sandbox (a system with no `sandbox-exec`); that strict mode fails the
same way in other HyperFrames versions (read on 0.8.101; run on 0.8.101 and 0.8.139), so a version bump should rerun the integration test, whose second case
is the throwing block; a person looking at the motion itself, not four frames of it.

## CLI

```
python -m hyperframes_mcp.motion list
python -m hyperframes_mcp.motion show credit-line
python -m hyperframes_mcp.motion lint
python -m hyperframes_mcp.motion lint src/hyperframes_mcp/assets/motion/blocks/credit-line.html
python -m hyperframes_mcp.motion install credit-line --dir PROJECT
python -m hyperframes_mcp.motion install credit-line --dir PROJECT --force
python -m hyperframes_mcp.motion tokens-css
python -m hyperframes_mcp.motion render credit-line --sheet --twice
python -m hyperframes_mcp.motion map-data germany --source DIR
python -m hyperframes_mcp.motion map-data europe --source DIR --out DIR
```

`layers`, `compose`, `build` and `master` work on a composed project and a shot list (`docs/motion-shot-list.md`, "Commands").

`render` is described under "Rendering and review". `list` and `show` print the registry; `lint` takes files or folders; `install` prints each file written, each file kept and a count;
`tokens-css` prints the stylesheet an install writes. `map-data` builds `assets/motion/data/map-REGION.js` (or into `--out`) from
the Natural Earth GeoJSON files in `--source`, after checking each file's sha256; it needs no network (the files and their
addresses are in `assets/motion/data/DATA.md`).

## What was verified, and what was not

Verified (2026-10-05): the unit tests in `tests/motion/`; `credit-line` installed into a scratch project and rendered with
`hyperframes@0.8.101` under the no-network sandbox of the render helper (`src/hyperframes_mcp/hfcli/cli.py`): 192 frames at 24 fps,
the credit appears in the lower left corner of the safe zone in IBM Plex Sans Condensed at 30 px on the scrim, a mount's
`data-variable-values` (right alignment, long text wrapping inside the safe zone) work, nothing is on screen before or after, and two
renders are byte-identical. HyperFrames' own `lint` gives 0 errors on the project and `validate` in headless Chrome gives no console error
and 4 text elements passing WCAG AA.

Not verified: the HyperFrames Studio preview; the limits of
`limits` and the reading time in a real edit (no checker); the compiler's handling of a literal font family (read, not run); a person
looking at the motion.
