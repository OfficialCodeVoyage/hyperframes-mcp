"""The ``hyperframes-edit`` MCP server: Claude Code edits and reviews composed video projects through typed tools.

It serves the chat of the HyperFrames desktop app (which loads the person's user-scoped MCP servers) and terminal
sessions alike, one project per call: a project is a composed HyperFrames folder, found by the tool's ``project``
argument, else the session's folder. The tools are thin: each calls a library function of ``hyperframes_mcp.motion`` (or the
budget, ledger and profile modules) and returns text with structured output. Spending is quoted, then confirmed, under
limits the server enforces itself, because the app runs its agent without permission prompts.
"""
