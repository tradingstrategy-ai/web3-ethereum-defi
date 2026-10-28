---
name: clean-up-for-pr
description: Clean up a completed feature, bug fix or maintained content change before PR handoff. Simplify the patch, correct code and prose, update agent and Sphinx documentation, check AGENTS.md conformance, and obtain an independent cross-agent review.
---

# Clean up for PR

Use this after implementation and focused checks, before reporting the work as
ready for review. It applies to code, documentation, skills and manually
maintained descriptions, notes and metadata. Run it once for the completed
change, rather than after every nested skill or individual file edit.
When reached from a skill invoked by another skill, leave cleanup to the
outermost task after all its implementation is complete. A child skill returns
its result to its caller without starting a separate cleanup or review.

Keep the user's intended behaviour and scope. Inspect nearby code where it
helps simplify or verify the change; avoid an unrelated repository-wide
refactor. Preserve other work already present in the worktree. Follow the
user's existing authorisation for commits, pushes, PRs and production actions.

## Establish the review scope

Read the applicable `AGENTS.md` instructions. Confirm the repository, branch
and worktree, then inspect tracked, staged and untracked changes:

```shell
pwd
git status --short --branch
git diff --stat
git diff --cached --stat
git diff --name-only
git diff --cached --name-only
git ls-files --others --exclude-standard
```

Include committed feature changes against the intended PR base when applicable.
Read new untracked source files explicitly: they are absent from `git diff`.
Inspect targeted hunks and the surrounding implementation rather than dumping
the entire patch into context.

## Clean up the implementation and text

1. **Simplify the code and relevant surrounding code.** Remove unnecessary
   abstractions, redundant state, duplicated work and speculative generality.
   Prefer existing helpers and direct control flow when they express the same
   behaviour more clearly. Keep safeguards and meaningful regression coverage.
2. **Correct misleading information.** Check comments, docstrings, error
   messages, examples, descriptions, notes and metadata against the actual
   behaviour and authoritative sources. Distinguish a published claim from a
   verified fact and a historical snapshot from current state. Correct factual
   errors without inventing missing information.
3. **Update documentation.** For database repairs or persisted accessor
   refreshes, check the [migration documentation](../../docs/migration-scripts.md)
   and require a new numbered migration or an explicit no-migration rationale.
   Standalone repair scripts are legacy format: do not recommend creating or
   invoking them, or rerunning a successful initial migration for a new change.
   Check that runbooks and PR reminders use the production host launcher.
   Revise relevant `.claude/docs` guidance when the work changes an agent
   workflow or reveals a reusable operational lesson.
   Update Sphinx narrative/API sources, examples and index references when
   their contracts or usage change. Keep public product copy focused on the
   product; put repository internals in technical documentation. Read the
   source files and check links and references; **do not build Sphinx docs** or
   edit generated `_autosummary*` files.
4. **Check repository conformance.** Audit touched code and tests against
   `AGENTS.md`, especially argument and return type hints, Sphinx `:param:` and
   `:return:` documentation, `dataclass(slots=True)`, `#:` member comments,
   logging, naive UTC datetimes and the repository's address/percentage types.
   Follow the prescribed Poetry, environment and focused-test commands.
5. **Review docstrings for rationale and mechanism.** Keep a concise summary,
   then explain why the function exists, how it achieves its purpose, and any
   meaningful constraints or failure behaviour. Document inputs and outputs;
   include canonical API links and DataFrame column/value expectations where
   relevant. Replace prose that merely translates the implementation into
   English. Avoid padding simple functions or repeating inherited docstrings.
6. **Remove AI slop.** Cut filler, hype, unsupported assurances, repetitive
   summaries, invented labels and comments that add no information. Use concrete
   language, UK English, `onchain`, and sentence-case headings. Keep manually
   maintained copy factual, sourced and appropriate to its audience.
7. **Obtain an independent review** using the opposite agent as described below.

## Review with the opposite agent

Before invoking any external agent CLI, read
[agent tricks and troubleshooting](../../docs/agent-tricks-and-troubleshooting.md)
in the current session and follow its invocation, permissions, streaming,
timeout and completion rules. That document owns the CLI recipes.

| Implementing agent | Required reviewer | Requested model |
|---|---|---|
| Codex | Claude CLI | Opus 5.5 (`--model claude-opus-5-5`) |
| Claude | Codex CLI | GPT 6.1 sol (`--model gpt-6.1-sol`; smoke-test availability) |

Resolve and smoke-test the exact requested model before a substantial review.
Do not silently substitute a moving alias or another model. If the requested
CLI/model cannot run after checking its setup, report the review as incomplete
with the concrete reason; finish the independent cleanup that remains possible.

Give the reviewer the task intent and reviewed file groups, including new
untracked files and relevant surrounding code. Require it to verify the worktree
and non-empty patch, read `AGENTS.md`, and inspect the files itself. Ask for
findings ranked by severity with file:line references and residual risks,
covering correctness, regressions, missing tests, misleading text and repository
instruction compliance. Request concrete simplifications only when they remove
demonstrated unnecessary complexity without changing the intended behaviour.

The reviewer must not edit files, run tests, change GitHub or production state,
invoke this cleanup skill, or start another reviewer. Only the implementing
agent runs the cleanup workflow; this prevents recursive review chains.

Verify each finding against the actual code and sources before applying it.
Fix substantiated issues within scope and re-run affected checks. Request a
focused follow-up if a fix changes behaviour or leaves a material concern
unresolved. Accept only a completed review with a final verdict, never a
partial event stream or an empty output file.

## Finish the handoff

Format only the reviewed Python paths, including reviewed new files, with
`poetry run ruff format path/to/changed.py path/to/new.py`. Run the relevant
focused checks and `git diff --check`. Inspect the final status and patch,
including staged and untracked files. Do not start a Sphinx build or the whole
test suite as a cleanup check.

Report the cleanup made, checks run, reviewer and model used, review outcome,
and any remaining limitations. Keep the repository's required PR description
format when a PR was requested. Do not claim PR readiness when a required
review or check remains incomplete.
