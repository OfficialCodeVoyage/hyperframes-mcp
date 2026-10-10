# Contributing

Thanks for helping. This project exists so that Claude Code and the HyperFrames desktop app can edit one project
together, so the most useful contributions are: keeping up with new builds of the app, fixing a tool that misbehaves,
and adding blocks.

## Set up

```bash
git clone https://github.com/OfficialCodeVoyage/hyperframes-mcp
cd hyperframes-mcp
uv venv && uv pip install -e ".[dev]"
.venv/bin/hyperframes-mcp setup      # the pinned HyperFrames CLI and browser libraries (a few tests need them)
.venv/bin/python -m pytest -q -n auto
.venv/bin/python -m ruff check .
```

Tests that need the real HyperFrames CLI, a browser or the running app are opt-in or skip themselves
(`HYPERFRAMES_MCP_REAL_CLI=1`, `HYPERFRAMES_MCP_RENDER_INTEGRATION=1`); nothing in the suite calls a paid API or needs a
key. `tests/server/fake_app.py` is a small fake of the app's endpoint: it is the contract the bridge is tested against.

To try your checkout in Claude Code: `hyperframes-mcp register --dev .` (it runs the code of the checkout instead of the
installed package); then quit and reopen the app, because its chat loads MCP servers only when it starts.

## Before you open a pull request

- `ruff check .` and the whole suite pass. Format only the files you changed (`ruff format path/to/file.py`).
- A change to a tool's name, arguments or description: run `python scripts/gen_tools_doc.py` (a test fails when
  `docs/tools.md` is stale).
- A change for a new app build: follow [`docs/app-compat.md`](docs/app-compat.md) and say what you tried.
- `python scripts/scan_tree.py` finds nothing: no key, token, personal path or e-mail address in what you add.
- Tests first for logic. A bug fix starts with a test that fails.

## Rules that are cheap to break and expensive to find out

- **A key or the app's token never leaves the process**: not in a log line, an error, a record file, a tool result or a
  test fixture. Tools say which keys exist by name only.
- **Paid calls go through `motion/spend.py` and nowhere else.** A new paid call needs a quote step, a confirm step, a
  price in the price table, and tests that it is held, released or recorded in each failure case
  ([`docs/spend-safety.md`](docs/spend-safety.md)).
- **Edits go through the app's history** (`bridge.history_begin` / `file_write` / `history_end`) so each one is a single,
  undoable entry under the author `hyperframes-mcp`. Never edit `index.html` outside that path; never undo an entry that
  is not ours.
- **Do not guess what the app does.** Its endpoint is undocumented: read the app's source with `scripts/extract_app.py`,
  and put what you learn in the fake app and in `docs/app-compat.md`.
- **Licences.** A new font, icon, texture, data file or block derived from someone else's code needs its licence text
  beside it and a row in the relevant `THIRD_PARTY.md`, with a pinned commit. Do not commit GSAP, or anything with a
  share-alike, non-commercial or no-derivatives licence.
- **Free media keeps its record.** A fetched file brings a `.json` licence record, and the credit line that shows it.

## Adding a block

The contract (tokens only, a declared variable list, a paused timeline, no randomness or clock) is in
[`docs/motion-library.md`](docs/motion-library.md). `python -m hyperframes_mcp.motion lint` checks it and
`python -m hyperframes_mcp.motion render NAME --sheet --twice` renders it twice and shows its frames.

## Commit messages and review

Say why, not only what. One logical change per commit. By contributing you agree that your contribution is licensed
under the Apache License 2.0 of this repository.
