# Agent tricks and troubleshooting

Read this document before invoking an external agent CLI from another agent.
It contains local invocation details that are easy to get wrong, especially for
non-interactive review runs.

This note covers practical ways to use **Claude CLI**, **Codex CLI**, and
**Grok CLI** (`grok`) as local engineering agents, especially when one agent is
used to review or debug another agent's work.

## Covered CLIs

These commands are in scope for this document and for the **Agents** gate in
`CLAUDE.md` / `AGENTS.md`:

| CLI | Typical non-interactive entrypoints |
|-----|-------------------------------------|
| Claude CLI | `claude`, `claude -p`, `claude ultrareview` |
| Codex CLI | `codex`, `codex exec` |
| Grok CLI | `grok` (Grok Build TUI; also headless / print-style prompts) |

Equivalent wrappers or aliases for the same tools are covered as well.

## Review rules (when one agent drives another)

Apply these whenever this file is required before an agent CLI run:

- For plan reviews with Claude CLI, default to the no-tools inline review pattern after the primary agent has inspected the relevant code. Only use a grounded tool-using review when fresh repository inspection is actually required.
- For code and PR reviews with Claude CLI, scope the request to correctness bugs, behavioural regressions, missing tests, security or money-movement risks, and repository instruction compliance. Ask for findings first with file:line references and residual risks. These reviews need repository inspection: do not use `--tools ""` or tell Claude not to use tools.
- For long Claude CLI reviews, use streaming output (`--output-format stream-json --verbose`) and a wall-clock timeout. If a grounded review produces no output after roughly one minute, stop it and repeat it for a smaller, tool-enabled file group. A no-tools review is only valid when the complete review material is embedded in the prompt, such as a plan or document.
- Do not paste huge diffs into Claude, Codex, or Grok prompts. Make the agent inspect `git status --short`, `git diff --stat`, `git diff --name-only`, and targeted hunks. Never ask it to dump a whole PR diff or run `gh pr diff | head -N` for a large `N`.
- For non-interactive Codex reviews, use `codex exec --json` in read-only mode. Plain text mode can buffer output and look hung.
- Before trusting any external-agent "no findings" result, verify it reviewed the correct worktree and non-empty diff. For a streaming Claude review, require a successful `result` event with a final verdict; tool calls, thinking events, and a live process are not a completed review.

## Codex CLI

Codex is useful for local repository work where the agent should inspect files, edit code, run tests, and keep working until a task is complete.

Common commands:

```shell
codex
codex "Explain this codebase"
codex exec "Review the current diff for correctness bugs"
codex exec --json "Summarise the failing tests"
codex review
codex doctor
codex resume --last
codex mcp list
codex plugin list
```

Useful capabilities:

- Interactive terminal UI for iterative coding and review.
- Non-interactive automation with `codex exec`.
- Local code review with `codex review` or `/review` inside the interactive UI.
- Git-aware operation over the current worktree.
- Sandbox and approval controls with `--sandbox` and `--ask-for-approval`.
- Machine-readable automation output with `codex exec --json`.
- MCP server management with `codex mcp`.
- Plugin management with `codex plugin`.
- Diagnostic checks with `codex doctor`.
- Session continuation with `codex resume`.

Recommended local patterns:

```shell
# Ask for a bounded review of local changes.
codex exec "Review uncommitted changes for correctness bugs only"

# Pass logs as context while keeping the prompt explicit.
poetry run pytest tests/foo.py -q 2>&1 \
  | codex exec "Explain the failure and suggest the smallest fix"

# Run with explicit permissions in automation. `codex exec` selects the sandbox
# directly and does not take `--ask-for-approval` (that is an interactive flag).
codex exec --sandbox workspace-write "Fix the failing focused test"

# Debug local setup.
codex doctor
```

Use `codex exec` for CI-like or scripted work. It streams progress to stderr and final output to stdout, which makes it easier to pipe the result into files or other commands.

Use interactive `codex` when the task needs back-and-forth decisions, screenshots, manual inspection, or careful approval of edits.

### Always run Codex reviews in streaming mode

Run every non-interactive Codex review (plan review, code review, sanity check)
in streaming mode with `--json`. Plain text mode (`codex exec "..."`) only emits
the final answer once the model has finished the entire review, and any
`| tail`, `| head`, or capture-to-file buffers that final block until the pipe
closes. When the run is backgrounded or captured, the output file then stays
**0 bytes** until completion — indistinguishable from a hang, and you cannot see
progress or interim tool calls.

`--json` instead emits a JSONL event stream (reasoning, tool calls, and the final
message) line-by-line as they happen, so a backgrounded run's file grows live and
can be tailed for progress.

```shell
# Streaming read-only review. Note: DO NOT pipe through `tail`/`head` — that
# reintroduces buffering. Write the raw JSONL stream to a file instead.
codex exec --json --sandbox read-only \
  "Review the uncommitted diff for correctness bugs only. Findings first with file:line." \
  > /tmp/codex-review.jsonl

# Follow progress live from another step (or when backgrounded):
tail -f /tmp/codex-review.jsonl        # interactive shells only

# Extract just the final assistant message from the JSONL when done:
#   each line is a JSON event; the final answer is the last agent/message event.
```

When backgrounding a Codex review, always use `--json` and read the raw output
file for interim events. If you instead run text mode in the background, the file
will look empty (0 bytes) the whole time and you will not be able to tell a slow
review from a stuck one.

Redirect stdin from `/dev/null` for background/non-interactive runs. With an open
stdin pipe, `codex exec` prints `Reading additional input from stdin...` and waits
for EOF (it appends piped stdin as a `<stdin>` block), so the run stalls forever
even though the prompt was passed as an argument. Always append `< /dev/null`:

```shell
codex exec --json --sandbox read-only "…prompt…" < /dev/null > /tmp/codex-review.jsonl
```

Approval flags: `codex exec` does **not** accept `--ask-for-approval` (that flag
belongs to interactive `codex`). For non-interactive review runs pick the sandbox
directly — `--sandbox read-only` needs no approval and is the correct choice for
reviews. Use `--sandbox workspace-write` only when the run must edit files.

```shell
# Correct non-interactive review invocation (streaming, read-only, no approval flag).
codex exec --json --sandbox read-only "Review uncommitted changes for correctness bugs only" \
  > /tmp/codex-review.jsonl
```

### Codex short model names (e.g. sol) need the full id — observed on one build

The following is a **version- and auth-specific observation**, not general Codex
guarantees: verified with `codex-cli 0.144.4` on a **ChatGPT-account** auth (see
`codex doctor` output — `stored auth mode: chatgpt`). Other builds/auth modes may
differ; re-check rather than assume.

Passing the bare short name `-m sol` failed two ways at once:

```text
Model metadata for `sol` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.
...
{"type":"error","status":400,"error":{"type":"invalid_request_error",
 "message":"The 'sol' model is not supported when using Codex with a ChatGPT account."}}
```

The **full model id `gpt-5.6-sol`** worked (confirmed by the smoke test below).
So when a user asks for a short name like "sol", try the `gpt-5.6-<name>` form:

```shell
# Failed — bare short name rejected on this build/auth.
codex exec --json --sandbox read-only -m sol "…" < /dev/null > /tmp/codex.jsonl

# Worked — full id, smoke-tested.
codex exec --json --sandbox read-only -m gpt-5.6-sol "…" < /dev/null > /tmp/codex.jsonl
```

`gpt-5.6-terra` and `gpt-5.6-luna` appear alongside `gpt-5.6-sol` in the binary
strings (below) as sibling family members; only `gpt-5.6-sol` was actually
smoke-tested here, so treat the `terra`/`luna` short-name → full-id mapping as
inferred, not confirmed.

Notes:

- Model availability depends on the auth mode. Check with `codex doctor` if that
  version supports it (look for `stored auth mode` / `auth mode` — `chatgpt` vs
  API key). Some ids that work on API-key auth are rejected on a ChatGPT account,
  and vice versa. Extracting strings from the binary only proves a string exists,
  not that the id is a usable model — always smoke-test.
- The build's candidate ids can be discovered from the binary when there is no
  `list-models` command:

  ```shell
  strings "$(command -v codex)" | grep -oE "gpt-5[a-z0-9.\-]*|o3|o4-mini" | sort -u
  ```

  This repository's Codex build (`codex-cli 0.144.4`, ChatGPT auth) exposed:
  `gpt-5.1`, `gpt-5.1-codex-max`, `gpt-5.1-codex-mini`, `gpt-5.2`,
  `gpt-5.2-codex`, `gpt-5.3-codex`, `gpt-5.4`/`-mini`/`-nano`, `gpt-5.5`,
  `gpt-5.5-pro`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, plus legacy
  `o3` and `o4-mini`.
- Always smoke-test a model id before a long review so a rejected id does not
  masquerade as a hang:

  ```shell
  codex exec --json --sandbox read-only -m gpt-5.6-sol "Reply with exactly: OK" < /dev/null
  ```

## Claude CLI

Claude CLI is useful for independent second opinions, code reviews, background agents, and checking whether another agent's change makes sense.

Common commands:

```shell
claude
claude -p "Review the current worktree diff"
claude -p "Review the current worktree diff" --output-format stream-json --verbose
claude auth status
claude ultrareview master --timeout 15
claude doctor
claude agents
claude mcp
claude plugin list
claude --help
```

Useful capabilities:

- Interactive Claude Code session by default.
- Non-interactive print mode with `-p` / `--print`.
- Streaming automation output with `--output-format stream-json`.
- Tool restrictions with `--allowedTools` and `--disallowedTools`.
- Permission mode control with `--permission-mode`.
- Custom or selected agents with `--agent` and `--agents`.
- Cloud-hosted multi-agent review with `claude ultrareview`.
- Safe mode for debugging broken customisations with `--safe-mode`.
- Bare mode for minimal startup with `--bare`.
- Debug logging with `--debug` or `--debug-file`.
- MCP and plugin management.

Important authentication note:

- Do not use `--bare` to check whether Claude is signed in. Bare mode skips
  keychain/OAuth credentials by design and only uses `ANTHROPIC_API_KEY` or an
  `apiKeyHelper` from `--settings`. A signed-in Unix user can therefore see
  `Not logged in` in `--bare` mode even though normal `claude -p` works.
- Use normal mode for local signed-in accounts:

```shell
claude auth status
claude -p "Say OK"
```

- Use `--safe-mode` instead of `--bare` when debugging broken customisations
  but you still want normal auth, model selection and built-in permissions to
  work.

Recommended local patterns:

```shell
# Smoke-test non-interactive auth and startup.
claude -p "Say OK"

# Plain one-shot review. Good when you can wait for final buffered output.
claude -p "Review the current git diff for correctness bugs"

# Better for long reviews: stream progress and tool calls.
claude -p "Review the current git diff for correctness bugs" \
  --output-format stream-json \
  --verbose

# Read-only PR review. Use the checked-in workspace permissions and permit
# repository inspection; the prompt prohibits state-changing actions.
claude -p "Review the current worktree diff. Do not edit files." \
  --model opus \
  --permission-mode dontAsk \
  --allowedTools "Bash,Read,Grep,Glob,WebSearch,WebFetch"

# Safer read-only review without broad bypass mode.
claude -p "Review the current worktree diff. Do not edit files. Findings first." \
  --permission-mode dontAsk \
  --allowedTools "Bash(git status:*),Bash(git diff:*),Bash(sed:*),Bash(rg:*)"

# Avoid pasting huge diffs into the prompt. Make Claude inspect files itself.
claude -p "Review uncommitted changes. First run git diff --name-only, then inspect targeted diffs."

# Run cloud review when account credits and PR/base context are available.
claude ultrareview master --timeout 15
```

For long-running `claude -p` jobs, prefer `--output-format stream-json --verbose`. Text mode can look idle because useful output may be buffered until the final answer.

If a broad review stalls, first verify that basic non-interactive mode and
read-only Bash tools work before assuming auth is broken:

```shell
claude -p "Say OK"
claude -p "Run git status --short and summarise it in one sentence." \
  --allowedTools "Bash(git status:*)"
```

If these work but the broad review times out, shrink the request: ask Claude to
inspect `git diff --name-only` first, then review one tool-enabled file group at
a time. Do not replace a PR review with a no-tools behavioural prompt: it lacks
the repository context needed for a review verdict. Use a no-tools prompt only
when it embeds the complete source material being reviewed.

### PR review tool-access smoke test

Before a costly PR review, prove that the current worktree can use the tools it
needs. The repository's `.claude/settings.json` grants local inspection, Bash,
GitHub and web tools, but a non-interactive command still needs an explicit
permission mode and tool allow-list. Require a final JSON result from this
smoke test before starting the review:

```shell
timeout 120 claude -p "Use Read to inspect AGENTS.md, Bash to run pwd and gh pr view --json url, and WebSearch to look up Claude Code. Do not edit files or change GitHub state. Summarise which tools succeeded." \
  --model opus \
  --permission-mode dontAsk \
  --allowedTools "Bash,Read,Grep,Glob,WebSearch,WebFetch" \
  --output-format json \
  --no-session-persistence
```

If it fails, run `claude doctor`, `claude auth status` and `gh auth status`.
Fix the reported trust, authentication or settings issue before reviewing. Do
not substitute a no-tools PR review: that cannot validate the repository.

### PR review permissions and workspace trust

The checked-in `.claude/settings.json` grants Claude the tools required for a
grounded PR review: local disk inspection, Git and `gh` access through Bash,
and web search/fetch. It deliberately also retains write permissions for normal
implementation work. A review prompt must still explicitly say **do not edit
files, change GitHub state, or run tests**.

`--allowedTools` is additive: it does not reliably remove a broader project
permission that is already present in `.claude/settings.json`. For a strictly
read-only review of already identified files, use `--safe-mode` and explicitly
deny mutation-capable tools. `--safe-mode` retains normal authentication; the
explicit denial is what enforces the narrow tool set:

```shell
nohup timeout 120 claude -p "Inspect only eth_defi/example.py and report correctness findings with file:line references. Use Read only. Do not make changes." \
  --model opus \
  --effort low \
  --safe-mode \
  --permission-mode dontAsk \
  --allowedTools "Read" \
  --disallowedTools "Bash,Edit,Write,MultiEdit,Skill,Task" \
  --output-format stream-json \
  --verbose \
  --no-session-persistence \
  < /dev/null > /tmp/claude-read-review.jsonl 2>&1 &
```

Use this strict pattern only when the primary agent has already established the
reviewed paths and diff. A grounded PR review that needs Git or GitHub commands
must retain Bash, but its prompt must name the allowed read-only commands and
the narrow file group.

Run the smoke test below before a costly review. It proves the actual current
worktree can use local disk, GitHub and web tools; do not claim a completed
review unless it returns a final result.

```shell
timeout 120 claude -p "Use Read to inspect AGENTS.md, Bash to run pwd and gh pr view --json url, and WebSearch to look up Claude Code. Do not edit files or change GitHub state. Summarise which tools succeeded." \
  --model opus \
  --permission-mode dontAsk \
  --allowedTools "Bash,Read,Grep,Glob,WebSearch,WebFetch" \
  --output-format json \
  --no-session-persistence
```

`claude -p` deliberately skips the interactive workspace-trust dialogue. This
is safe only for a trusted repository, and prevents an unattended review from
stalling on that dialogue. Do not use `--bare`: it disables normal OAuth and
keychain authentication. Do not use `--dangerously-skip-permissions` for an
internet-connected review; the project permission allow-list above provides the
needed access without bypassing every safety control.

If the smoke test fails, run `claude doctor`, `claude auth status` and
`gh auth status`, then fix the reported authentication or settings-validation
problem before reviewing. Never report that a trust configuration prevented a
review: either resolve it and obtain a final review result, or report the review
as incomplete.

### Foreground command-window limits

Some agent runners end foreground output capture after roughly 30 seconds,
even when Claude is actively reading files and emitting streaming JSON. The
Claude child may then continue after its output pipe has disappeared, causing
the outer command to report an empty completion. This is neither a successful
review nor evidence of an authentication failure: its final answer cannot be
recovered from that foreground capture. Do not rely on a foreground `claude -p`
invocation for a non-trivial worktree review in those environments.

Start the review in the background and write the raw JSONL stream to a temporary
file instead. Restrict tools to read-only operations and redirect stdin so the
CLI cannot wait for additional prompt input:

Use a 15-minute wall-clock deadline for an external-agent process, including
Claude CLI and Codex CLI. This gives a legitimate review enough time to inspect
the worktree; it does not override the separate one-minute no-output rule for a
grounded Claude review.

```shell
nohup timeout 900 claude -p "Review the current uncommitted worktree diff for correctness bugs only. Do not edit files, change GitHub state, or run tests. First inspect git status --short, git diff --stat, and git diff --name-only. Review only the resulting relevant file group with targeted diffs; do not dump the full diff. Return findings first with file:line references." \
  --model opus \
  --effort low \
  --permission-mode dontAsk \
  --allowedTools "Bash,Read,Grep,Glob,WebSearch,WebFetch" \
  --disallowedTools "Edit,Write,MultiEdit,Skill,Task" \
  --output-format stream-json \
  --verbose \
  --no-session-persistence \
  --debug-file /tmp/claude-review.debug.log \
  < /dev/null > /tmp/claude-review.jsonl 2>&1 &
```

Poll the process and inspect the raw file from later commands. Do not pipe the
Claude process through `head` or `tail`, as that can interfere with streaming:

```shell
ps -p <pid> -o pid=,stat=,etime=,cmd=
rg '"type":"result"' /tmp/claude-review.jsonl
tail -n 40 /tmp/claude-review.jsonl
```

Only trust the review after the JSONL file contains a successful ``result``
event with the final findings. If the command reaches its timeout without that
event, narrow the file scope and repeat the background review rather than trying
to resume a `--no-session-persistence` session. Do not describe a partial event
stream as a review result, and do not fall back to a no-tools review unless the
complete target content is supplied in its prompt.

If the JSONL stream stops growing for one minute, terminate the process, keep
the JSONL and debug log for diagnosis, and start a new smaller review. Inspect
the debug log for the last dispatched tool and server errors; do not paste or
commit it because it can include repository context. A clean `claude doctor`
and `claude auth status`, together with a successful small detached Read-only
review, means the broad prompt or tool scope is the likely problem.

### Reviewing a plan or document with Claude CLI

For Markdown plan reviews, default to a no-tools inline text review after the
relevant code has already been inspected by the primary agent. Do not start
with a grounded tool-using Claude review for simple plan re-reviews; it can
sit silently in `-p` mode or wait internally on tool/permission handling, and
that repeats avoidable delays.

Use this for ordinary plan re-review:

```shell
claude -p "$(sed '1iDo not use tools. Review only the plan text below. Return concise actionable findings, or say no blocking findings.\n' .claude/plans/my-plan.md)" \
  --tools "" \
  --permission-mode dontAsk \
  --no-session-persistence \
  --max-budget-usd 1
```

Use this for a final blocking-only pass after applying review feedback:

```shell
claude -p "$(sed '1iDo not use tools. Review only the updated plan text below. Return only blocking findings, or say no blocking findings.\n' .claude/plans/my-plan.md)" \
  --tools "" \
  --permission-mode dontAsk \
  --no-session-persistence \
  --max-budget-usd 1
```

Only use a grounded repository review when Claude specifically needs fresh code
inspection, for example when the primary agent has not checked the relevant
files or when the plan makes claims that need independent verification against
the worktree. In that case, allow only read-only tools and make the scope
explicit. This exception applies to plan reviews only; code and PR reviews are
always grounded reviews:

```shell
claude -p "Review .claude/plans/my-plan.md for correctness and completeness. Focus on implementation risks, missing code paths, and test gaps. Keep the review concise and actionable." \
  --allowedTools Read,Grep,Glob,Bash \
  --permission-mode dontAsk \
  --output-format stream-json \
  --verbose
```

If a grounded review produces no output after roughly a minute, stop it and
switch to the no-tools inline review unless fresh repository inspection is
strictly required.

Notes:

- Use `--tools ""` only when the prompt embeds all necessary context. Never use
  it for a code or PR review that needs to inspect the worktree or a diff.
- If you allow tools, use comma-separated tool names for `--allowedTools`.
- `--permission-mode dontAsk` avoids interactive permission prompts in
  non-interactive review runs.
- `--no-session-persistence` keeps one-off reviews from polluting later
  `claude --continue` sessions.
- `--max-budget-usd` is optional but useful for bounded document reviews.

## Grok CLI

Grok (xAI) is a third cross-agent reviewer. It is useful for an independent
second opinion on plans, diffs and PRs, and for grounded repository inspection.
It runs headlessly with `-p` / `--single` and returns machine-readable JSON.

Common commands:

```shell
grok                                  # interactive TUI
grok -p "Review the current diff"     # headless single-turn, prints to stdout
grok models                           # list models (and the default)
grok agent headless                   # agent over the WebSocket relay
grok --help                           # full flag list
```

Model selection:

- Pass the model with `-m` / `--model`, e.g. `--model grok-4.5`.
- `grok models` prints the available ids and the default. Do not guess ids —
  list them first. As of this writing the default/only id is `grok-4.5`.

Key headless flags for reviews:

- `-p, --single "<prompt>"` — one-shot prompt, prints the answer and exits.
  Prefer `--prompt-file <path>` (or `-p "$(cat file)"`) for long prompts.
- `-m, --model grok-4.5` — the reasoning model.
- `--output-format streaming-json` — emit JSONL events as Grok reasons, calls
  tools, and writes its answer. **Use this for every review.** The final
  `{"type":"end", ...}` event carries `stopReason`, `sessionId`, `requestId`,
  and usage. Plain `json` emits one object only after the whole review and can
  leave a captured file looking empty while it is working; reserve it for short
  one-turn smoke tests.
- `--permission-mode <mode>` — `plan`, `dontAsk`, `default`, `acceptEdits`,
  `auto`, `bypassPermissions`. A grounded headless review that must inspect a
  PR needs `bypassPermissions` **and** `--always-approve`; `plan` and
  `dontAsk` can cancel after the model's first tool request because no
  interactive user is present to resolve it.
- `--cwd <dir>` — set the working directory (the tree to review).
- `--disable-web-search` — turn off web tools for a code-only review.
- `--disallowed-tools <names>` — comma-separated built-in tools to remove
  (e.g. `edit,write` to forbid mutations).
- `--always-approve` — auto-approve tool calls. Together with
  `--permission-mode bypassPermissions`, and with neither `--tools` nor
  `--disallowed-tools`, exposes every installed Grok tool to the review.
- `--no-plan --no-subagents` — prevent project plan/review skills and spawned
  agents from expanding a bounded, single-turn review.
- `--max-turns <N>` — cap the review agent's tool-use turns. Use `48` for a
  non-trivial grounded PR review; lower values such as `2` or `3` are suitable
  only for a smoke test or a deliberately tiny, no-tools prompt.
- `--reasoning-effort <low|medium|high>` (alias `--effort`).

### Grounded PR reviews need all tools and a background process

Use this pattern when the reviewer must inspect an actual pull request. The
Grok process has every tool available, but the prompt is still explicitly
read-only. This is appropriate only in a trusted repository: the permission
flags grant the process authority, not merely read access.

Use `gh` rather than `git` for pull-request interaction. It makes the PR number
and remote base explicit, prevents a stale local branch from being reviewed,
and lets the reviewer inspect `gh pr view` and `gh pr diff` directly.

Do **not** run a non-trivial Grok review in the foreground. Some command
runners terminate a foreground process after about 30 seconds, before Grok
emits its final JSONL `end` event. Streaming JSON makes the live file grow with
thought, tool and answer events, so a foreground termination no longer looks
like a successful review; it can still discard the final findings. A shell
`timeout` alone does not prevent this outer termination; detach the process and
poll it instead:

```shell
# Prepare the prompt with a reliable file tool, then start a fully enabled,
# read-only-by-instruction PR review. Do not add --tools or --disallowed-tools.
nohup timeout 900 grok --prompt-file /tmp/grok-pr-review.md \
  --model grok-4.5 \
  --reasoning-effort high \
  --permission-mode bypassPermissions --always-approve \
  --no-plan --no-subagents --max-turns 48 \
  --output-format streaming-json \
  --cwd "$(pwd)" \
  < /dev/null > /tmp/grok-pr-review.jsonl 2>/tmp/grok-pr-review.err &
review_pid=$!

# Poll from later commands. The JSONL file should grow while Grok is working.
# Do not use `tail -f` in non-interactive automation.
ps -p "$review_pid" -o pid=,stat=,etime=,cmd=
wc -c /tmp/grok-pr-review.jsonl /tmp/grok-pr-review.err
tail -n 30 /tmp/grok-pr-review.jsonl

# Only accept a review after its final JSONL event has completed with EndTurn.
python3 -c "import json; events=[json.loads(line) for line in open('/tmp/grok-pr-review.jsonl')]; end=events[-1]; assert end['type'] == 'end' and end['stopReason'] == 'EndTurn'; print(''.join(event['data'] for event in events if event['type'] == 'text'))"
```

Open `/tmp/grok-pr-review.md` with instructions equivalent to:

```text
Review GitHub PR #<number> read-only. Do not edit files, change GitHub state,
or run tests. Use gh—not git—for every repository/PR interaction: first run
gh pr view #<number> --json number,title,state,baseRefName,headRefName, then
gh pr diff #<number> --name-only. Do not fetch an unfiltered full diff. Inspect
the reviewable code with `gh pr diff #<number> --color=never --patch` and
`--exclude` globs for generated artefacts and unrelated plans. Return findings
first with file:line references and fixes.
```

Generated ABI JSON, large plans, lockfiles and other mechanically produced files
can consume the whole review context and turn budget. For example, first review
source and test changes while excluding known generated/non-code paths:

```shell
gh pr diff 1388 --color=never --patch \
  --exclude '*.json' \
  --exclude '.claude/plans/**' \
  --exclude 'docs/**'
```

If a generated file is security-relevant, review it separately after its source
change; do not make it part of the initial broad pass. If the narrowed review
still reaches `max turns` after the 48-turn budget, split the changed files
into two prompts rather than raising the limit indefinitely. Treat `Error: max
turns reached` as an incomplete review, never as a no-findings result.

### Always check `stopReason`, and fall back to a no-tools review

A grounded Grok review can **stop before it writes any findings** — the JSON
then contains only the preamble ("I'll review …") and the final streaming
event's `stopReason` is `Cancelled` (or anything other than `EndTurn`). In particular, an observed
`cancellationCategory: PermissionCancelled` occurred when a headless
`--permission-mode plan` run selected the repository's review skill and then
needed a permission decision. This is neither an authentication failure nor a
valid review result. A large worktree can also consume the limited turns while
navigating files.

So, use the fully enabled background command above for a grounded review.
**If it produces only a preamble, or `stopReason != "EndTurn"`, first confirm
that the detached process reached its timeout or completed.** Only then switch
to a bounded no-tools review that pastes the highest-risk code directly into the
prompt. The no-tools form removes both filesystem permissions and repository
navigation:

```shell
# No-tools review: embed the exact code to review; tell it not to read files.
timeout 240 grok -p "$(cat /tmp/notools-review.txt)" \
  --model grok-4.5 --no-plan --no-subagents --tools '' \
  --disable-web-search --output-format streaming-json \
  > /tmp/grok-out.jsonl 2>/tmp/grok-err.log
python3 -c "import json; events=[json.loads(line) for line in open('/tmp/grok-out.jsonl')]; print(events[-1]['stopReason']); print(''.join(event['data'] for event in events if event['type'] == 'text'))"
```

Build the no-tools prompt from `sed -n 'START,ENDp' file` excerpts of the
functions under review (settlement drivers, money-movement paths, the changed
hunks), and open the prompt with an explicit instruction such as
"NO TOOLS — review only the code below and reply with a findings report." Keep
each excerpt small so the whole prompt stays well under the model's turn budget.

### Scope Grok reviews like the other CLIs

Same policy as Codex/Claude reviews: scope the request to correctness bugs,
behavioural regressions, missing tests, security or money-movement risks, and
repository-instruction compliance. Ask for findings first, ranked by severity,
with file/line references and a residual-risk verdict. Because a no-tools review
only sees the pasted excerpts, its findings can be **false positives from
truncated context** (e.g. "this check is dead code" when the guarding
`if` was above the excerpt). Always re-verify each Grok finding against the real
file before acting on it.

### Grok gotchas

- **Prove the CLI path before a costly review.** On the currently installed
  alpha build, `grok models` can say "You are not authenticated" even while a
  cached CLI session works. Do not treat that command alone as an auth verdict.
  Run this smoke test instead; require both a non-empty JSONL file and
  `EndTurn`:

  ```shell
  timeout 60 grok -p 'Reply with exactly: OK. Do not use tools.' \
    --model grok-4.5 --no-plan --no-subagents --tools '' \
    --disable-web-search --output-format streaming-json --cwd "$(pwd)" \
    > /tmp/grok-smoke.jsonl 2>/tmp/grok-smoke.err
  python3 -c "import json; events=[json.loads(line) for line in open('/tmp/grok-smoke.jsonl')]; assert events[-1]['type'] == 'end' and events[-1]['stopReason'] == 'EndTurn'; print(''.join(event['data'] for event in events if event['type'] == 'text'))"
  ```

  If this fails, use `grok login --device-auth` on a headless host, or
  `grok login --oauth` from an interactive terminal. Never print or copy
  `~/.grok/auth.json` while diagnosing authentication.
- **Writing the prompt file.** A `cat > /tmp/foo` heredoc can hit
  `Permission denied` in a sandboxed shell. Write the prompt with a reliable
  file tool or to a repo-local path, then pass it with `--prompt-file` or
  `-p "$(cat …)"`.
- **A blank file is a failed invocation, not a clean review.** Capture stderr,
  check the detached process with `ps`, run the smoke test, then repeat once
  with `--debug-file /tmp/grok-debug.log`.
  Search that file for `cancel`, `PermissionCancelled`, `auth`, and `error`, but
  do not paste raw debug logs because they can contain credentials or repository
  content. Do not run `grok update` automatically; investigate first and update
  only with the user's approval.
- **`max turns reached` means the reviewer had too much material, not that its
  tools failed.** Start from `gh pr diff --name-only`, exclude generated and
  narrative files, set `--color=never`, and review one source/test group at a
  time. The default 48-turn recipe is intentionally generous enough for a
  focused PR; a larger PR needs separate review passes.
- **Do not combine headless reviews with plan mode or partial tool lists.**
  `--permission-mode plan` can select a plan/review skill and lead to a
  non-interactive permission cancellation. `--tools` is an allow-list, so it
  can remove the terminal, `gh`, or a supporting inspection tool. For a
  grounded PR review, omit both `--tools` and `--disallowed-tools`; use the
  fully enabled command above and constrain behaviour in the prompt instead.
- **Verify the tree and diff.** As with any external agent, confirm Grok
  reviewed the intended worktree (`--cwd`) and a non-empty diff before trusting
  a "no findings" result (see "The agent reviews the wrong tree").
- **Clean up.** Remove `/tmp/grok-*.json` and prompt files after the run.

## Cross-agent review patterns

Use the other agent as a reviewer when:

1. The change touches state accounting, execution, security, or money movement.
2. The first agent wrote a large test or complex fixture.
3. You want a second model to challenge assumptions before opening a pull request.
4. You suspect the first agent is stuck in a local optimum.

Good review prompt:

```text
Review the current uncommitted worktree diff for correctness bugs only.
Do not run the full test suite.
Do not paste the full git diff into context.
First inspect git status --short, git diff --name-only, and targeted diffs.
Focus on behavioural regressions and test fragility.
Return findings first with file:line references.
If there are no high-confidence bugs, say so clearly and list residual risks.
```

Avoid asking for broad "thoughts" on a large diff. Ask for a scoped review:

- correctness bugs
- behavioural regressions
- missing tests
- test fragility
- security or money-movement risks
- repository instruction compliance

## Grok CLI

Grok CLI is available locally as `grok`. Use its headless single-turn mode for
an independent review. As with Claude and Codex, do not let a review agent edit
the worktree.

Verify the installed command and its current flags before changing an existing
recipe, because Grok CLI releases can change options:

```shell
grok --help
grok --version
```

For a bounded, read-only review, disable memory, web search and subagents. Use
`streaming-json` so progress is visible, and save the raw stream rather than
piping it through `tail` or `head`:

```shell
timeout 900 grok -p "Review the current uncommitted diff for correctness bugs only.
Do not edit files or run the full test suite. First inspect git status --short,
git diff --name-only, and targeted diffs. Return findings first with file:line
references. If there are no high-confidence bugs, say so clearly." \
  --tools "" \
  --permission-mode dontAsk \
  --sandbox read-only \
  --disable-web-search \
  --no-memory \
  --no-subagents \
  --max-turns 12 \
  --output-format streaming-json \
  < /dev/null > /tmp/grok-review.jsonl
```

Do not use `--always-approve`, `--permission-mode bypassPermissions`, or
`--fs-write` for a review. Grok 0.2.93 has an internal error when headless mode
builds the terminal tool (``auto_background_on_timeout`` is incompatible with
its disabled background setting). Use the toolless mode above and include a
focused review bundle through `-p`; the `--prompt-file` form was cancelled after
its planning turn in 0.2.93. For a prepared bundle at
`/tmp/grok-review-input.txt`, invoke it as
`grok -p "$(sed -n '1,$p' /tmp/grok-review-input.txt)" ...` without
`--no-wait-for-background`, because that option cancels multi-turn reasoning
before findings are returned. Do not request repository inspection until the
installed version changes. Keep the 15-minute outer deadline and terminate a
no-output review after checking the raw stream and process state.

If the installed Grok cannot enforce ``--sandbox read-only`` because Bubblewrap
is unavailable, use a sandbox-free run only for a genuinely toolless review
(``--tools ""``) with the complete review text embedded in the prompt. Do not
apply this fallback to a repository-inspecting review.

## Common failure modes

### The command looks hung

Symptoms:

- `claude -p` prints nothing for a long time.
- The terminal appears idle, but the process is still alive.

Causes:

- Text output is buffered until the final answer.
- The model is doing a long review or reading a large diff.
- The prompt caused the agent to paste a huge diff into context.
- A subprocess is waiting for input or a permission decision.

Avoid it:

```shell
claude -p "Review the current diff" --output-format stream-json --verbose
```

For Codex automation, use:

```shell
codex exec --json "Review the current diff"
```

Also constrain the prompt:

```text
Do not paste the full diff into context. Use git diff --name-only first, then inspect targeted hunks.
```

### The review consumes too much context

Symptoms:

- The model reads `git diff` for a large change and slows down.
- Output includes truncated tool results.
- The final answer misses important details.

Avoid it:

- Start with `git diff --stat` and `git diff --name-only`.
- Inspect changed files with `sed`, `nl`, `rg`, or targeted `git diff -- path`.
- Ask the reviewer to avoid full diff dumps.
- Split reviews by topic or file group.

Better prompt:

```text
Review only eth_defi/research/vault_metrics.py and tests/research/test_vault_metrics.py first.
Then inspect tests only if needed to validate coverage.
```

### The agent cannot use tools

Symptoms:

- Claude says it cannot inspect files.
- Codex refuses to edit or run commands.
- A non-interactive run exits after a permission problem.

Avoid it:

- For Codex, set the sandbox explicitly (`codex exec` selects the sandbox
  directly; it has no `--ask-for-approval` flag):

```shell
codex exec --sandbox workspace-write "Run the focused test and fix failures"
```

- For Claude, set explicit permission mode and allowed tools:

```shell
claude -p "Read-only review" \
  --permission-mode bypassPermissions \
  --dangerously-skip-permissions \
  --allowedTools "Bash,Read,Grep,Glob"
```

Use broad bypass modes only in trusted repositories or externally sandboxed environments.

### The cloud review does not start

Symptoms:

- `claude ultrareview` exits immediately.
- Error mentions usage credits or account limits.

Example:

```text
Ultrareview could not launch: Usage credits exhausted.
```

Avoid it:

- Fall back to local `claude -p`.
- Use streaming output for visibility.
- Narrow the review prompt to reduce cost.
- Run local focused tests yourself and include the results in the final assessment.

### The agent reviews the wrong tree

Symptoms:

- Findings refer to the parent repository instead of the worktree.
- Tests import parent source instead of worktree source.
- The branch name or status does not match expectations.

Avoid it:

```shell
pwd
git status --short --branch
git rev-parse --show-toplevel
```

For this repository's worktrees, run commands from the target worktree, use the
parent repository Poetry environment unless changing package dependencies, and
force imports from the target worktree. Follow the test command rules from
`AGENTS.md` and always verify the working directory and branch before trusting
review output:

```shell
pwd
git status --short --branch
source .local-test.env && PYTHONPATH="$(pwd):$PYTHONPATH" poetry run pytest tests/path/to/test.py
```

### The agent misses repository instructions

Symptoms:

- Test docstrings do not follow repo rules.
- Commands omit `source .local-test.env`.
- Python style diverges from `AGENTS.md`.

Avoid it:

- Tell the reviewer to read `AGENTS.md` first.
- Cite the relevant instruction in the prompt.
- Ask specifically for "AGENTS.md compliance" as a review axis.

Good prompt:

```text
Read AGENTS.md first. Review the new pytest tests for repository instruction compliance:
docstring format, comments matching steps, type hints, and command invocation assumptions.
```

### The agent runs too much

Symptoms:

- Full test suite starts unexpectedly.
- Long-running fork tests or Docker pulls start during review.
- CI-like commands exceed local time budgets.

Avoid it:

- Say "do not run the full test suite".
- Name the exact tests that may be run.
- For review-only work, restrict tools to read-only commands.

Example:

```text
Do not run tests. Inspect the code and tell me what focused tests should be run.
```

### The agent changes files during a review

Symptoms:

- A review command edits files.
- Formatting or unrelated cleanup appears in `git diff`.

Avoid it:

- For Claude, omit edit tools from `--allowedTools`.
- For Codex, ask for review only and use read-only sandbox:

```shell
codex exec --sandbox read-only "Review uncommitted changes for correctness bugs"
```

### Output is not machine-readable

Symptoms:

- Scripts cannot reliably parse the answer.
- Progress messages are mixed with final output.

Avoid it:

- Codex: use `codex exec --json` for JSONL event streams.
- Claude: use `claude -p --output-format json` for one result or `stream-json` for live events.
- Ask for a schema when stable fields are needed.

Claude example:

```shell
claude -p "Return {\"findings\": [...], \"risk\": \"...\"}" \
  --json-schema '{"type":"object","properties":{"findings":{"type":"array"},"risk":{"type":"string"}},"required":["findings","risk"]}'
```

### Authentication or MCP setup is broken

Symptoms:

- `doctor` reports missing auth.
- MCP servers show `needs-auth`.
- Tools that depend on external services are absent.
- `claude --bare -p "Say OK"` says `Not logged in`.

Avoid it:

```shell
codex doctor
codex mcp list
claude auth status
claude -p "Say OK"
claude doctor
claude mcp
```

Do not diagnose normal Claude CLI auth with `--bare`; it intentionally skips
keychain/OAuth credentials. Use `claude auth status` and a normal `claude -p`
smoke test instead.

Do not assume missing MCP tools are model limitations. Check installation, auth, workspace policy, and whether the session needs restarting after a config change.

## Practical checklist

Before launching another agent:

1. Confirm the working directory and branch.
2. Decide whether the task is interactive, non-interactive, or cloud review.
3. Restrict tools if it is a review.
4. Prefer streaming JSON for long non-interactive jobs.
5. Tell the agent not to paste huge diffs.
6. Name the exact risk areas to review.
7. Ask for file:line findings and residual risks.
8. Run focused tests yourself when the reviewer cannot.

After the agent finishes:

1. Separate high-confidence findings from speculation.
2. Verify any proposed bug against the code.
3. Apply only fixes that match the original task.
4. Re-run focused tests if code changed.
5. Record useful failure modes in this document.
