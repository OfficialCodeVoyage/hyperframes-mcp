# HyperFrames app compatibility

This server talks to the HyperFrames desktop app through an endpoint the app does **not** document: `POST /mcp` on
`127.0.0.1` (the port is in the app's `studio-port.json`, the bearer token in its `mcp.json`), plus a small `/selection`
route. A new build of the app can change it without notice, so this page records what was tried and how to check a new
build quickly.

## Builds tried

The build number is `hfDatadog.version` in the `package.json` inside `app.asar` (the bundle version stays `0.1.0`).
`hyperframes-mcp doctor` prints it and warns about a build that is not in this table.

| Build | Result | Notes |
|---|---|---|
| b276 | worked | the first build the server was developed against |
| b296 | worked, 2026-10-10 | `tools/list` offers 38 tools; every call the bridge makes answered; a job carries `threadIds` (see below). Not yet run live: `resolve_comment` on a comment whose Apply run is queued |

## What the bridge relies on

- **Tools** (`REQUIRED_APP_TOOLS` in `src/hyperframes_mcp/server/appinfo.py`): `project.list`, `file.read`, `file.write`,
  `history.begin`, `history.end`, `history.list`, `history.undo`, `comments.list`, `comments.post`, `comments.resolve`,
  `agent.jobs`, `preview.frame`.
- **The selection route** (`/api/projects/<id>/selection`) for `resolve_selection`.
- **The history author.** The app takes the author of a history entry from the first token of the request's `User-Agent`;
  the bridge sends `hyperframes-mcp/1`, so its edits show under `hyperframes-mcp` and `undo` finds only those.
- **Jobs and comments.** `agent.jobs` lists the app's agent runs; a run names the comments it carries. From b296 a job
  has `threadIds` (several comments sent together reach the agent as one request); earlier builds named a single `threadId`.
  `resolve_comment` refuses to resolve a comment that a queued or running job carries, because resolving it would end
  that run.
- **What the app's own server offers.** `KNOWN_APP_TOOLS` is the reviewed list of the 38 tools. The app also has a
  run-scoped route (`/mcp/image/<job>`) that it hands only to its own agent runs; it carries `app.look` and HeyGen's
  generation tools (`generate_video`, `generate_speech`, `generate_avatar_video`, `create_voice_clone`, which bill the
  HeyGen key in the app's settings). It is not reachable through `register --with-app`, which only adds the 38-tool
  endpoint.

## Checking a new build

1. Update the app, open it once, then run `hyperframes-mcp doctor`. It reports the build, whether the endpoint answers
   and any tool the app offers that is not in `KNOWN_APP_TOOLS`.
2. `python scripts/diff_app_tools.py` compares the installed app's source with the reviewed tool list (no app needs to
   run); `--live` asks the running app instead and is the authoritative list. It exits 1 when a tool the bridge calls
   is gone, when a tool is new, or when a reviewed tool disappeared, and names each.
3. `python scripts/extract_app.py OUT_DIR` writes the app's `main/` source for reading. Read `mcpServer.mjs`,
   `mcp*Tools.mjs`, `comments.mjs` and `agentJobs.mjs` for changed names, schemas and fields.
4. Run the suite, then try each tool against the running app on a copy of a project (no spend needed): `project_info`,
   `layer_table`, `catch_up`, `post_comment`, `list_comments`, `edit_timeline` then `undo`, `get_frame`, `run_checks`.
5. Add the build to `TESTED_BUILDS` and to the table above; add any reviewed tool to `KNOWN_APP_TOOLS` (the fake app in
   `tests/server/fake_app.py` serves that set) and say in the pull request what you tried.

Open an issue with the "app build" template when something breaks on a build not listed here.
