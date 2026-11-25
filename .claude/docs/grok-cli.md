# Grok CLI reviews

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
  list them first.

Key headless flags for reviews:

- `-p, --single "<prompt>"` — one-shot prompt, prints the answer and exits.
  On builds with working prompt-file support, use `--prompt-file <path>` (or `-p "$(cat file)"`) for long prompts.
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

## Compatibility

Grok 0.2.93 failed to initialise its headless terminal tool, and `--prompt-file` could stop after the planning turn. On that version, pass a bounded review with `-p`, disable tools and require an `EndTurn` event. Omit `--no-wait-for-background`, which ended multi-turn reasoning before the reviewer returned its findings. If the read-only sandbox requires an unavailable Bubblewrap installation, only use a sandbox-free invocation with tools disabled.

## Pull request reviews

Use this pattern only on a build with working headless tools and when unrestricted tool access has been authorised. The permission flags grant access to every installed tool. For the affected older build, use the [compatibility instructions](#compatibility).

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

- **Prove the CLI path before a costly review.** In an earlier alpha build, `grok models` can say "You are not authenticated" even while a
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
  a "no findings" result (see [checking the worktree](agent-tricks-and-troubleshooting.md#the-agent-reviews-the-wrong-tree)).
- Remove only the temporary files created by your review.
