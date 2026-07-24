# Agent tricks and troubleshooting

Read this document before invoking Claude CLI or Codex CLI from another agent.
It contains local invocation details that are easy to get wrong, especially for
non-interactive Claude review runs.

This note covers practical ways to use Codex CLI and Claude CLI as local engineering agents, especially when one agent is used to review or debug the other agent's work.

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

# Restrict tools for a read-only review.
claude -p "Review the current worktree diff. Do not edit files." \
  --permission-mode bypassPermissions \
  --dangerously-skip-permissions \
  --allowedTools "Bash,Read,Grep,Glob"

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
inspect `git diff --name-only` first, review one file group at a time, or provide
a concise summary of the proposed fix instead of embedding a large diff.

### Foreground command-window limits

Some agent runners terminate a foreground command after roughly 30 seconds,
even when Claude is actively reading files and emitting streaming JSON. This
can leave a review with only tool calls and no final findings. Do not rely on a
foreground `claude -p` invocation for a non-trivial worktree review in those
environments.

Start the review in the background and write the raw JSONL stream to a temporary
file instead. Restrict tools to read-only operations and redirect stdin so the
CLI cannot wait for additional prompt input:

Use a 15-minute wall-clock deadline for an external-agent process, including
Claude CLI and Codex CLI. This gives a legitimate review enough time to inspect
the worktree; it does not override the separate one-minute no-output rule for a
grounded Claude review.

```shell
nohup timeout 900 claude -p "Review the current uncommitted worktree diff for correctness bugs only. Do not edit files or run tests. Return findings first with file:line references." \
  --permission-mode dontAsk \
  --allowedTools "Read,Grep,Glob,Bash(git status:*),Bash(git diff:*),Bash(sed:*),Bash(rg:*)" \
  --output-format stream-json \
  --verbose \
  --no-session-persistence \
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
to resume a `--no-session-persistence` session.

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
explicit:

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

- Use `--tools ""` only when the prompt embeds all necessary context.
- If you allow tools, use comma-separated tool names for `--allowedTools`.
- `--permission-mode dontAsk` avoids interactive permission prompts in
  non-interactive review runs.
- `--no-session-persistence` keeps one-off reviews from polluting later
  `claude --continue` sessions.
- `--max-budget-usd` is optional but useful for bounded document reviews.

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
