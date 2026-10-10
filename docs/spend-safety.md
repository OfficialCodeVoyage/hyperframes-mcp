# Spend safety

Claude calls a paid provider (OpenAI or fal.ai, with **your** keys) only through this server, and the HyperFrames app runs
its agent without asking permission first. So the limits are not a request to the agent: they are checks in the server's
code that run before every paid call. This page says what they cover, what they do not, and how to tighten them.

## The flow of a paid call

1. **Quote** (`quote_picture`, `quote_clip`): nothing is paid. The quote holds the price, the full prompt, and a 15-minute
   expiry. A quote above any limit is refused already.
2. The agent tells you what it will make and the price.
3. **Confirm** (`confirm_spend`): the price is worked out again; the call is refused if anything moved (price, prompt,
   model, profile, the still), if the quote expired, if the file it would write already exists (a paid file is never
   written over), if a limit would be passed, if paid calls are frozen, or if your host program's budget refuses it.
4. **Hold**: the price is written to the spend logs as a `reserved` line *before* anything is sent. A line belongs to
   one quote and one attempt number; the next line of that attempt replaces it in the counts.
5. The call is made. The hold then becomes the spend (`confirmed`, at the real cost), or is released, or is kept as
   `uncertain` (see below). A process that dies after sending leaves the `reserved` line, which is a hold too.

What became of a quote is read from the **logs** (the central one in your data folder and the project's own, together;
where they disagree about an attempt the central one wins, because it is written first), never from the quote file in
the project: that file is writable by the agent, so it holds only what was quoted, and each entry in it must be filed
under its own id. A quote
the logs show as spent or held can never be sent again, whatever the quote file says. A quote whose attempts were all
released may be sent again, `max_tries_per_quote` times in all.

## What each guard is for

| Risk | Guard |
|---|---|
| A runaway loop, or an injected instruction to "make fifty pictures" | `per_call_usd`, `per_project_usd`, `per_day_usd`, `all_projects_per_day_usd` |
| The price table is wrong or out of date | `max_calls_per_hour` and `max_calls_per_day` count calls, not dollars, so a wrong price cannot run away; every attempt counts, the ones the provider refused too; a call that cost more than its quote is recorded in full and reported; a cost the provider reports that is not a plain number is recorded at the quoted price |
| The app is quit, the process is killed, or a call times out, after the request was sent but before the answer came back: it may be billed and was never recorded | the **hold**: the price stays counted (`reserved` or `uncertain`) until you answer `hyperframes-mcp spend clear QUOTE --billed` or `--not-billed` after looking at the provider's dashboard |
| A retry of that call bills twice | a quote with a spend or a hold in the logs can never be confirmed again; the picture client does not retry a dropped connection (only a rate limit, which is refused before billing) and the SDK's own retries are off |
| Two videos pay at the same moment and both pass the daily limit | one paid call at a time across every project and process (a lock) |
| Someone deletes a project's `.video-mcp` folder to start counting from zero | the central log in `~/.hyperframes-mcp/data/spend.jsonl` counts every project |
| A confirm waits for another paid call and the day turns over meanwhile | the clock is read after the wait for the lock, not before, so the new day's spend is seen |
| A project's own log cannot be written (a folder put where the file is) | before the call that stops it, with nothing sent; after the call the central log has the line, the host budget is still told, and the answer carries a note |
| A project folder is renamed, or its path is spelled in another case, after a call left a hold | the central log also names a project by its folder's identity (device and inode), so its holds and spend follow the folder |
| The central log cannot be written after a paid call | the project's own log and the host budget still get the spend, and the answer carries a note; the central log keeps the call's reservation, which counts as a hold until you clear it |
| A reservation could be written to the central log but not to the project's | it is released again in the central log before the error: a hold that nothing sent cannot use up the day's budget |
| Your answer for a hold lands in the middle of a running call | `spend clear` waits for the paid-call lock (10 s), so it cannot |
| The file of a paid call is made a link, or taken, while the provider works | the file is created only where nothing is, never through a link; if the name was taken the paid result is kept as `name.paid-1.ext` and the answer says so |
| A spend log is damaged (a line cut short by a full disk, an amount that is not a plain number) | nothing can be quoted or confirmed, and the message names the file and line; `spend status` and `spend clear` still work. Mend or remove the line |
| You need everything to stop now | `hyperframes-mcp spend freeze`, or `HYPERFRAMES_MCP_SPEND` set to anything but `on` in the server's environment: every confirm is refused, quoting still works; `spend thaw` undoes the file (unset the variable to undo that) |

A failure is *released* (nothing was billed) only when that is known: the package's own checks (no key, a clip length the
model cannot make, a cap), a provider answer in the 4xx range, or an error the code that made the call marked as not
billed (a policy refusal fal gave when the job was submitted). Everything else is *uncertain*: timeouts, dropped
connections, 5xx, a failure while saving the result, a policy failure found after fal accepted the job (fal bills
those), a failure after the provider answered, a stopped process. The safe assumption about a request that may have
run is that it did.

## The variables

The limits live in `limits.yaml` (copy it to `~/.hyperframes-mcp/config/limits.yaml` and edit the copy). A variable in
the server's environment can only **lower** a limit, or set one the file left empty; it can never raise one:

| Variable | Limit |
|---|---|
| `HYPERFRAMES_MCP_PER_CALL_USD` | `per_call_usd` |
| `HYPERFRAMES_MCP_PER_PROJECT_USD` | `per_project_usd` |
| `HYPERFRAMES_MCP_PER_DAY_USD` | `per_day_usd` |
| `HYPERFRAMES_MCP_ALL_PROJECTS_PER_DAY_USD` | `all_projects_per_day_usd` |
| `HYPERFRAMES_MCP_MAX_CALLS_PER_HOUR` | `max_calls_per_hour` |
| `HYPERFRAMES_MCP_MAX_CALLS_PER_DAY` | `max_calls_per_day` |
| `HYPERFRAMES_MCP_SPEND` | any value but `on` (or unset) freezes every paid call |

The app's chat does not see a shell's variables, so put them in the registered entry:

```bash
hyperframes-mcp register --env HYPERFRAMES_MCP_PER_PROJECT_USD=5 --env HYPERFRAMES_MCP_MAX_CALLS_PER_DAY=40
```

`hyperframes-mcp spend status` shows the limits in force, today's calls and dollars, and every held call.
`hyperframes-mcp doctor` has a `spend` line.

## What this does not cover

- **An agent that runs shell commands or writes files can edit `limits.yaml`, the logs, a project's manifest or its quote
  file**, because they are files on your disk, and the app's chat agent runs with those tools. The limits stop mistakes,
  loops, drift of a price, crashes and injected instructions that go through the tools; they are not a security
  boundary against an agent that sets out to get around them. Treat them as guard rails. Things that follow from it:
  deleting the central log makes the all-projects and call-count limits forget (a project's own log keeps its dollars);
  a project's `video.project.json` names the profile whose limits apply (`spend.profiles.<name>` in `limits.yaml`), so an
  agent can pick another profile you configured; and `per_project_usd`, the project's `per_day_usd` and the clip cap are
  counted per project folder, so a new folder starts them again. Only the all-projects daily dollars and the call counts
  bind across folders, and nothing here caps spend across days.
- **The provider's own limit is the real backstop.** Set a monthly spending cap in your OpenAI and fal.ai accounts;
  nothing here replaces it.
- **A file made while a paid call runs can still be redirected by changing a folder above it.** The file itself is created
  only where nothing is and never through a link, but the project folders on its path are the agent's to rearrange; the
  spend is held and counted either way, and the harm is a picture or clip written in the wrong place.
- **A call you answered for as billed has no file.** `spend clear QUOTE --billed` records the money, not a result; that
  quote is used up, and a new picture needs a new quote.
- **The data folder must be the same for you and the server.** `hyperframes-mcp spend status` prints the folder it reads
  (`HYPERFRAMES_MCP_HOME` moves it). If the server was registered with a different `HYPERFRAMES_MCP_HOME` than your shell
  has, `spend freeze` in your shell freezes a folder the server does not look at.
- **Costs are the package's price table, not a bill.** A call is recorded at the cost the provider client reports from that
  table. The table can be out of date (`gpt-image-1` is announced to be retired on 2026-10-23: check the model in
  `config/settings.yaml`). The call limits above are what contain a wrong price.
- **The HyperFrames app's own paid features are not behind this server.** HeyGen's generation tools (video, speech,
  avatars) are given only to the app's own agent runs, on a route of their own, and they bill the HeyGen key saved in the
  app. They are not on the endpoint `register --with-app` adds (`doctor` lists the tools that endpoint offers and warns
  about any it has not seen before). To stop them, remove the HeyGen key in the app's settings.
