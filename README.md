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

The full list of the 30 tools is [`docs/tools.md`](docs/tools.md).

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
