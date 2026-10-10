# hyperframes-mcp

An [MCP](https://modelcontextprotocol.io) server that lets **Claude Code edit a HyperFrames project while you edit the
same project by hand in the HyperFrames desktop app.** You trim a clip, Claude fixes the credit line over it; both of you
work on one timeline, and every change Claude makes is an entry in the app's own history that you can undo.

> **Unofficial.** This project is not affiliated with, endorsed by or sponsored by HeyGen. It talks to the HyperFrames
> desktop app through a local endpoint the app does not document, so a new build of the app can break it. See
> [`docs/app-compat.md`](docs/app-compat.md) for the builds it has been tried against.

## What it does

- **Reads the project** the way the person sees it: `layer_table` (what is on screen when, with its credits and the
  sentence spoken over it), `narration` (the time of each spoken word), `get_frame` and `contact_sheet` from the app's
  own preview.
- **Edits it through the app.** Move, trim, split or delete layers; change a block's text, number or colour; add a
  counter, chart, title or stamp; place a layer on a spoken word. Each edit is one history entry under the author
  `hyperframes-mcp`, and `undo` takes back only Claude's own edits, never yours.
- **Works with you.** `resolve_selection` tells Claude what you are pointing at, `catch_up` what you changed since it
  last looked, and `post_comment` pins a note at a time on your timeline.
- **Finds free media** in the Library of Congress, Wikimedia Commons, the Internet Archive and Openverse, keeping a
  licence record and a true credit line for every file, plus an icon subset and vector tools.
- **Makes pictures and clips with your own key** (fal.ai: Nano Banana 2 for pictures, an image-to-video model for clips). Every paid call is quoted first, confirmed second,
  and checked against limits the server enforces. See [Spend safety](#spend-safety).

## Available tools

<!-- tools:start -->

The 30 tools of the `hyperframes-edit` server, as `/mcp` in Claude Code lists them. Each one is described in full, with its arguments, in [`docs/tools.md`](docs/tools.md) (generated from the server). *Read-only* tools change nothing; *open-world* ones reach outside your machine (the free archives, or the paid provider).

**Look.** Read the project. Nothing here changes it.

| Tool | What it does | Marked |
|---|---|---|
| `ping` | Whether the server runs, which version, which keys it can see (names only). | read-only |
| `project_info` | The project, its house rules, sources and the spend left. Claude calls it first. | read-only |
| `layer_table` | What is on screen when, with its credits and the sentence spoken over it. | read-only |
| `narration` | The time each spoken word is said, to land a layer on its word. | read-only |
| `block_info` | What each block is for, where it sits on the frame, and a layer's variables. | read-only |
| `style_info` | The profile's picture style and house rules. | read-only |
| `spend_status` | What has been spent against the limits, and what is left. | read-only |

**Talk to the app.** Work with what the person is doing in the HyperFrames desktop app. These need the app running.

| Tool | What it does | Marked |
|---|---|---|
| `resolve_selection` | What you are pointing at in the app: the time, the layers, the sentence. | read-only |
| `catch_up` | What changed since Claude last looked, and who changed it. | read-only |
| `post_comment` | Pin a note at a time on your timeline. |  |
| `list_comments` | The comments on your timeline, with their status. | read-only |
| `resolve_comment` | Mark a timeline comment resolved, or open it again. |  |
| `get_frame` | One frame of the video, from the app's own preview. | read-only |
| `contact_sheet` | A labelled grid of frames over a stretch, to review it at a glance. | read-only |

**Edit.** Change the project. Each edit is one history entry in the app, under the author `hyperframes-mcp`.

| Tool | What it does | Marked |
|---|---|---|
| `edit_timeline` | Move, trim, split, duplicate or delete layers (a dry run first). |  |
| `set_variable` | Change a block's text, number, colour or position. |  |
| `add_block` | Add a counter, ledger, chart, title, date stamp or lower third. |  |
| `arrange_layer` | Move or resize a layer. |  |
| `replace_media` | Put a picture or clip in its layer, with its credit line. |  |
| `add_graphic` | Place a saved vector (an icon, arrow, seal or stamp) as a new layer. |  |
| `undo` | Take back Claude's own last edits, never yours. |  |
| `run_checks` | Check the page after an edit: a true credit line with every picture, no layer hidden behind another. | read-only |

**Free media.** Photos from the free archives, and vectors. Nothing here is paid.

| Tool | What it does | Marked |
|---|---|---|
| `search_media` | Find CC0 and public-domain photos in four free archives. | open-world |
| `fetch_media` | Download the one you pick, with its licence record. | open-world |
| `search_icons` | Find an icon in the bundled open sets. | read-only |
| `save_vector` | Save an SVG in the project, after a safety check. |  |
| `vectorize` | Trace a picture of the project into an SVG, on your machine. |  |

**Paid pictures and clips.** Quote first, then confirm. The limits are enforced by the server: see `docs/spend-safety.md`.

| Tool | What it does | Marked |
|---|---|---|
| `quote_picture` | Price a new picture. Nothing is spent. |  |
| `quote_clip` | Price a short clip made from a picture. Nothing is spent. |  |
| `confirm_spend` | Make the quoted picture or clip, under the server's limits. Spends money. | open-world |

<!-- tools:end -->

## Requirements

- macOS with the [HyperFrames](https://github.com/heygen-com/hyperframes) desktop app (tested on builds b276 and b296)
- [Claude Code](https://claude.com/claude-code)
- Python 3.11 or newer, Node.js 22 or newer, and ffmpeg with ffprobe

## Install

```bash
uv tool install "git+https://github.com/OfficialCodeVoyage/hyperframes-mcp@v0.1.0"
hyperframes-mcp setup      # installs the pinned HyperFrames CLI and the block library's browser libraries (npm ci)
hyperframes-mcp register   # adds the server to Claude Code at user scope
hyperframes-mcp doctor     # says what works and what does not; prints no key or token
```

Then quit the HyperFrames app completely (Cmd+Q) and open it again: the app's chat loads MCP servers only when it starts.

`pipx install` works the same way. For the vector tracer add the extra: `"hyperframes-mcp[vectorize] @ git+https://github.com/OfficialCodeVoyage/hyperframes-mcp@v0.1.0"`.

**As a Claude Code plugin instead:** with the command installed as above, `/plugin marketplace add OfficialCodeVoyage/hyperframes-mcp`
then `/plugin install hyperframes-edit --marketplace OfficialCodeVoyage/hyperframes-mcp`. Use one of the two ways, not both.

**Optional:** `hyperframes-mcp register --with-app` also adds the app's own server (`hyperframes-desktop`), which gives
every chat the app's tools, its media finders included. Those finders bring no licence record, so it is off by default.
It puts the app's token in Claude Code's config; treat that config as a secret.

### Keys

Only the paid pictures and clips need a key, and one is enough: `FAL_KEY` (fal.ai) draws the pictures with Nano Banana 2
and makes the clips. `OPENAI_API_KEY` is needed only if you point `config/settings.yaml` at an OpenAI picture model.
The server reads them from the environment, then
`~/.hyperframes-mcp/config/.env`, then the macOS Keychain (service `hyperframes-mcp`). Claude never sees a key: tools
say whether one is set, by name. To keep one in the Keychain:
`security add-generic-password -s hyperframes-mcp -a FAL_KEY -w` (it asks for the value).

## Use it

The server works on a **composed project**: a HyperFrames folder with a `video.project.json` beside `index.html`. This
package composes one from a *shot list* (blocks and their variables, cued to the narration's words). From a clone of
this repository, the shipped example makes one in a few seconds:

```bash
python examples/hello/make_assets.py          # a silent stand-in for the narration
python -m hyperframes_mcp.motion compose examples/hello/shotlist.json \
    --alignment examples/hello/words.json --assets examples/hello/assets --out hello-project --open
```

`--open` hands the folder to the HyperFrames app. Open the project's chat in the app and ask Claude, for example: *"put
the credit line on the word 'ledger'"*. [`docs/motion-shot-list.md`](docs/motion-shot-list.md) explains the shot list
and [`docs/motion-library.md`](docs/motion-library.md) the blocks. A project composed before the manifest existed is
adopted with `hyperframes-mcp adopt`. Rendering stays with you, in the app; `python -m hyperframes_mcp.motion master`
sets the loudness of the file you export.

## Prompts to copy into your AI agent

Paste any of these into Claude Code, or into the HyperFrames app's chat once the server is registered and the app has been
restarted. Replace what is in `<angle brackets>`. The tools named are the ones in [Available tools](#available-tools).

**Install it and connect it to Claude Code**

```text
Install hyperframes-mcp and connect it to Claude Code. Run these one at a time and show me each result:
  uv tool install "git+https://github.com/OfficialCodeVoyage/hyperframes-mcp@v0.1.0"
  hyperframes-mcp setup
  hyperframes-mcp register
  hyperframes-mcp doctor
Never read, print or ask me for an API key or the HyperFrames app's token. Then tell me what doctor flagged, one line
each with what to do about it, and remind me to quit the HyperFrames app completely (Cmd+Q) and open it again.
```

**Start a session**

```text
You have the hyperframes-edit tools. Call project_info first and follow its house rules. Then call catch_up, so you
never write over anything I changed, and layer_table for the first 30 seconds. Tell me in a few lines what is on screen
and what is said over it. Change nothing yet.
```

**Fix what I am pointing at** (send it from the card on the app's timeline, so the message carries what you picked)

```text
Fix the layer I picked: <what is wrong and what it should be>. Pass the <app_context> of this message to
resolve_selection and tell me which layer and which sentence you found. Then change it with edit_timeline or
set_variable. Run edit_timeline as a dry run first and show me what would change before you apply it. If I picked
nothing, ask me to pick it on the timeline.
```

**Land a layer on a spoken word**

```text
Put the credit line on the word "<word>". Find when it is said with narration, show me the change as a dry run, apply
it with dry_run=false, then look at that second with get_frame and run run_checks.
```

**Review the video before I render it**

```text
Review the video before I render it. Run run_checks, then contact_sheet over the whole video, and get_frame at anything
that looks wrong. For each problem, pin a note at its time on my timeline with post_comment: say what to change, name
the layer, and pass change=free or change=paid. Do not edit anything and do not render.
```

**Find a free photo**

```text
I need a photo of <subject> for the layer <layer id>. Use search_media and tell me what you found, with each licence.
Wait for my pick, then fetch_media it and put it in the layer with replace_media. For a licence from the Internet
Archive or Openverse, remind me to confirm it at the source page.
```

**Make a new picture** (this spends money)

```text
Make a new picture of <subject, place, era and framing> for the layer <layer id>. Call quote_picture and tell me what
you will make and its price. Wait for my yes before confirm_spend. Then look at the picture and tell me whether it
matches the house look before you put it in the layer with replace_media. Do not try to get around the spend limits,
and never repeat a paid call on your own.
```

**See what has been spent**

```text
Call spend_status and tell me what this video and all my projects have spent today against the limits. Say if any paid
call is held.
```

**Take back your last edits**

```text
Undo your last <2> edits with undo. Tell me which history entries you took back. Leave my own edits alone.
```

**Check my setup**

```text
Run hyperframes-mcp doctor and explain every warning or failure in one line, with the command that fixes it. Never print
a key or the app's token.
```

**After a HyperFrames app update** (for a maintainer, from a clone of this repository)

```text
The HyperFrames desktop app updated. Run python scripts/diff_app_tools.py and tell me which tools changed. If it fails,
run python scripts/extract_app.py <an empty folder outside this repository> and read main/mcpServer.mjs against
docs/app-compat.md to find what the bridge calls that moved. Do not guess what the app does: read its source. Propose
the fix and the new row for docs/app-compat.md; do not push.
```

## Spend safety

The app runs its agent without asking permission first, so the limits are not a request to the agent: they are checks in
the server's code before every paid call. By default a call may cost at most $1, a video $10 and all your projects together $10 a day,
and at most 30 paid calls an hour (100 a day). A call that may have been billed (a timeout, a quit app) is
held against the limits until you check the provider and run `hyperframes-mcp spend clear`. `hyperframes-mcp spend
freeze` stops every paid call at once. Environment variables can only **lower** a limit. What this covers and what it
cannot (an agent with a shell can edit files; set a cap at your provider too) is in
[`docs/spend-safety.md`](docs/spend-safety.md).

## Docs

| | |
|---|---|
| [`docs/tools.md`](docs/tools.md) | every tool, generated from the server |
| [`docs/spend-safety.md`](docs/spend-safety.md) | the limits, the hold, the freeze switch |
| [`docs/app-compat.md`](docs/app-compat.md) | app builds tried; how to check a new one |
| [`docs/motion-library.md`](docs/motion-library.md) | the blocks and their contract |
| [`docs/motion-shot-list.md`](docs/motion-shot-list.md) | the shot list, `compose`, `build`, `master` |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues: [`SECURITY.md`](SECURITY.md).

## Licence

Apache-2.0, see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE). Fonts, icons, map data and some blocks carry their own
open licences ([`THIRD_PARTY.md`](THIRD_PARTY.md)). The GSAP animation library that the blocks load is **not** open
source (GSAP Standard "no charge" licence): it is installed by `hyperframes-mcp setup`, never copied into this repository.
