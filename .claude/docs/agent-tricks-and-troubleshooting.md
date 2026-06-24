# Agent tricks and troubleshooting

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

# Run with explicit permissions in automation.
codex exec --sandbox workspace-write --ask-for-approval never "Fix the failing focused test"

# Debug local setup.
codex doctor
```

Use `codex exec` for CI-like or scripted work. It streams progress to stderr and final output to stdout, which makes it easier to pipe the result into files or other commands.

Use interactive `codex` when the task needs back-and-forth decisions, screenshots, manual inspection, or careful approval of edits.

## Claude CLI

Claude CLI is useful for independent second opinions, code reviews, background agents, and checking whether another agent's change makes sense.

Common commands:

```shell
claude
claude -p "Review the current worktree diff"
claude -p "Review the current worktree diff" --output-format stream-json --verbose
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

Recommended local patterns:

```shell
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

# Avoid pasting huge diffs into the prompt. Make Claude inspect files itself.
claude -p "Review uncommitted changes. First run git diff --name-only, then inspect targeted diffs."

# Run cloud review when account credits and PR/base context are available.
claude ultrareview master --timeout 15
```

For long-running `claude -p` jobs, prefer `--output-format stream-json --verbose`. Text mode can look idle because useful output may be buffered until the final answer.

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
Review only tradeexecutor/cli/testtrade.py and tradeexecutor/strategy/pandas_trader/position_manager.py first.
Then inspect tests only if needed to validate coverage.
```

### The agent cannot use tools

Symptoms:

- Claude says it cannot inspect files.
- Codex refuses to edit or run commands.
- A non-interactive run exits after a permission problem.

Avoid it:

- For Codex, set explicit sandbox and approval flags:

```shell
codex exec --sandbox workspace-write --ask-for-approval never "Run the focused test and fix failures"
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

For this repository's worktrees, run tests through the parent Poetry environment but force worktree imports:

```shell
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

Avoid it:

```shell
codex doctor
codex mcp list
claude doctor
claude mcp
```

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
