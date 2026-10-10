# Security

## Report a problem

Please report a vulnerability privately through GitHub's "Report a vulnerability" button on this repository's Security
tab, not in a public issue. Include the build of the HyperFrames app (`hyperframes-mcp doctor` prints it), your version
of this package and what you saw. You should get an answer within a week.

## What this server touches

- **The app's local endpoint.** The HyperFrames desktop app listens on `127.0.0.1` and takes a bearer token that it keeps
  in `mcp.json` in its support folder. The bridge reads that file and sends the token only to that address. The token is
  scrubbed from the errors the bridge raises, `doctor` never prints it, and `register --with-app` puts it in Claude Code's
  config only when you ask for it (it is a secret in that file: treat the file like a password file).
- **API keys.** `OPENAI_API_KEY` and `FAL_KEY` are read from the environment, `~/.hyperframes-mcp/config/.env` or the
  macOS Keychain (service `hyperframes-mcp`). Tools report which keys exist by name, never a value. Nothing writes a
  key to a log, a record file or a project.
- **Spend.** A paid call goes through a quote and a confirm, and the server refuses it above its limits. The limits are
  code in the server, not a request to the agent. See [`docs/spend-safety.md`](docs/spend-safety.md) for what that does
  and does not cover. An agent that also has a shell on your machine can edit files, including the limits: set a
  spending cap at OpenAI and fal.ai as well.
- **Files.** Tools write inside the project folder you point them at. Downloaded media goes through size and type checks
  and keeps a licence record. Archives from outside (an app archive read by `scripts/extract_app.py`) are extracted with
  path checks.

## Supported versions

The latest release. The app's endpoint is undocumented and can change in any build: run `hyperframes-mcp doctor` after
an app update.
