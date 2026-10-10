# Changelog

All notable changes. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- The README lists the 30 tools in a table generated from the server (checked by `scripts/gen_tools_doc.py --check`) and has a
  section of prompts to copy into an AI agent.

## [0.1.0]

First public release, extracted from a private video project.

### Added
- The `hyperframes-edit` MCP server: 30 tools to read a composed HyperFrames project, edit its timeline through the
  app's own history (one entry per edit, author `hyperframes-mcp`, `undo` takes back only its own), pin comments on the
  person's timeline, and review frames and checks (`docs/tools.md`).
- A block library and a composer that turns a shot list into a HyperFrames project, with an offline command line
  (`python -m hyperframes_mcp.motion list|show|lint|install|render|compose|build|master|layers`) and a runnable example
  (`examples/hello`).
- Free media: search and fetch from the Library of Congress, Wikimedia Commons, the Internet Archive and Openverse with
  a licence record per file; an icon subset and vector tools.
- Paid pictures and clips with your own fal.ai key (Nano Banana 2 for pictures; OpenAI models can be configured), quoted first and confirmed second.
- Spend safety (`docs/spend-safety.md`): per-call, per-video and per-day dollar limits, call-count limits, a hold for a
  call that may have been billed, tighten-only environment variables, a freeze switch.
- `hyperframes-mcp register | setup | doctor | spend | adopt | new-chat`, a Claude Code plugin manifest, and
  `scripts/diff_app_tools.py` / `scripts/extract_app.py` to check a new build of the app.
- Tested against HyperFrames app builds b276 and b296 (`docs/app-compat.md`).
