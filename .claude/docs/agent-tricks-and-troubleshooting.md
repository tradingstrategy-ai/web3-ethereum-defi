# Agent tricks and troubleshooting

Read this document before invoking an external agent CLI from another agent.
It contains local invocation details that are easy to get wrong, especially for
non-interactive review runs.

## Covered CLIs

These commands are in scope for this document and for the **Agents** gate in
`CLAUDE.md` / `AGENTS.md`:

| CLI | Typical non-interactive entrypoints |
|-----|-------------------------------------|
| Claude CLI | `claude`, `claude -p`, `claude ultrareview` |
| Codex CLI | `codex`, `codex exec` |
| Grok CLI | `grok`, `grok -p` |
| Kimi Code CLI | `kimi -p` |

Equivalent wrappers or aliases for the same tools are covered as well.

Besides reviews, the monthly vault report runs Claude CLI or Codex CLI
unattended and unsandboxed, with web search, for its investability check. The
exact commands live in `eth_defi.vault_report.vault_checks.build_agent_command()`
and the [report guide](../../eth_defi/vault_report/README-vault-report.md#investability-check) documents the decision rounds.

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

### Non-interactive reviews

Redirect stdin so a background process cannot wait for input. Text mode can leave the log empty until the review finishes. Write the JSON event stream directly to a file and inspect that file from a second command. Piping the reviewer through `head` or `tail` can close the pipe early or hide progress.

```shell
timeout 900 codex exec --json --sandbox read-only "Review the current worktree diff. Do not run tests or read environment files." < /dev/null > /tmp/codex-review.jsonl
```

`--sandbox read-only` blocks writes, not reads. A review on 6 November 2026 left
its worktree and tried to `source` the main checkout's `.local-test.env`. Prohibit
env and secrets files in the prompt, and check the JSONL afterwards. Pytest also
fails at setup in this sandbox because it has no writable temporary directory.

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

The **full model id `gpt-5.6-sol`** worked (confirmed by the smoke test below)
on that installation. This historical observation does not establish the ID
or availability of a newer requested model. Use the explicitly requested full
model ID and smoke-test it; do not replace its version with `5.6`.

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
- On `codex-cli 0.155.1` (ChatGPT auth, 2026-09-25), `-m gpt-6-sol` was accepted
  without a fallback-metadata warning and completed a full review, even though
  the string does not appear in the installed npm package. Binary strings are
  therefore not a complete list of usable ids; the smoke test is the check.
- A `--sandbox read-only` Codex review cannot run this repository's pytest:
  collection fails because Matplotlib needs a writable cache or temporary
  directory. Run the focused tests yourself and tell the reviewer so, instead
  of asking it to run them.

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

`claude -p` deliberately skips the interactive workspace-trust dialogue. This
is safe only for a trusted repository, and prevents an unattended review from
stalling on that dialogue. Do not use `--bare`: it disables normal OAuth and
keychain authentication. Do not use `--dangerously-skip-permissions` for an
internet-connected review; the project permission allow-list above provides the
needed access without bypassing every safety control.

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
CLI cannot wait for additional prompt input.

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
claude -p "$(sed '1iDo not use tools. Review only the plan text below. Return concise actionable findings, or say no blocking findings.\n' /tmp/my-plan.md)" \
  --tools "" \
  --permission-mode dontAsk \
  --no-session-persistence \
  --max-budget-usd 1
```

Use this for a final blocking-only pass after applying review feedback:

```shell
claude -p "$(sed '1iDo not use tools. Review only the updated plan text below. Return only blocking findings, or say no blocking findings.\n' /tmp/my-plan.md)" \
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
claude -p "Review /tmp/my-plan.md for correctness and completeness. Focus on implementation risks, missing code paths, and test gaps. Keep the review concise and actionable." \
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

See [Grok CLI reviews](grok-cli.md) for model checks, permissions, JSON output and recovery when a headless review stops early.

## Kimi Code CLI

Observed with `kimi` 2.0.2 (`~/.kimi-code/bin/kimi`) on 2026-09-25.

- Non-interactive runs use `-p "prompt"` with `--output-format stream-json`.
  Attach extra read-only context directories with `--add-dir`, e.g. the website
  frontend checkout.
- The model is chosen with `-m <alias>` from `~/.kimi-code/config.toml`.
  K3 is `kimi-code/k3`, which defaults to `high` effort. There is no CLI flag for
  the reasoning effort; it comes from the alias's `default_effort`. For max
  effort, add a separate alias rather than editing the managed one. The session
  log records the effort as `"thinkingEffort":"max"`:

  ```toml
  [models."kimi-code/k3-max"]
  provider = "managed:kimi-code"
  model = "k3"
  max_context_size = 1048576
  capabilities = [ "thinking", "always_thinking", "image_in", "video_in", "tool_use", "dynamically_loaded_tools" ]
  support_efforts = [ "low", "high", "max" ]
  default_effort = "max"
  ```

- Tell Kimi explicitly that the review is read-only. It reads files and runs
  shell commands itself.
- A grounded plan review at max effort took about 45 minutes. Run it in the
  background and poll the JSONL file. The run is finished when the final
  `session.resume_hint` event appears; the review is the last assistant message
  without tool calls.

```shell
nohup kimi -m kimi-code/k3-max --add-dir /path/to/frontend -p "$(cat prompt.txt)" \
  --output-format stream-json < /dev/null > /tmp/kimi-review.jsonl 2> /tmp/kimi-review.err &
```

## Cross-agent review patterns

For a completed feature, bug fix or maintained content change, run
[clean-up-for-pr](../skills/clean-up-for-pr/SKILL.md). The skill owns the
cleanup checklist and requested reviewer models: Codex uses Claude CLI with
Opus 5.5; Claude uses Codex CLI with GPT 6.1 sol. Resolve and smoke-test the
exact requested model; generic `opus` examples and older model observations in
this document do not override that selection. The invocation, tool access,
streaming, timeout and result-validation guidance here still applies.

On 2026-10-28, a local smoke test with `--model claude-opus-5-5` returned
`OK` and reported that exact model in its initial event. This verifies Opus
5.5 for the account used in that run; other accounts still need the smoke test.
`gpt-6.1-sol` is the requested full Codex ID for this workflow and must be
smoke-tested in the invoking Claude session before a substantial review.

Include staged changes and untracked source files as well as unstaged diffs.
Tell the reviewer not to invoke cleanup skills or start another reviewer, so
the implementing agent remains responsible for a single completed review.

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

Check the permission settings in the [Claude CLI instructions](#claude-cli) or the [Codex CLI instructions](#codex-cli). For a repository review, verify that the reviewer can inspect the worktree and its diff.

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
