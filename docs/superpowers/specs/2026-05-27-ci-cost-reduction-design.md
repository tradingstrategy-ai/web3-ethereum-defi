# CI cost reduction design

## Why

Issue #1034 tracks GitHub Actions minutes exceeding the Tradingstrategy organisation's 3000-minute monthly allowance. The current repository runs a broad `Automated test suite` on most pull request updates, including changes that do not need the expensive fork/RPC test surface. The May 4-10 spike data shows many 6-9 minute `Automated test suite` runs and repeated cancelled or failed runs, so the fastest useful reduction is to avoid unnecessary runs before attempting deeper test reorganisation.

This work should be delivered in one PR, but as staged commits. Each stage must include a cost comparison command so reviewers can compare current observed cost with the estimated or observed cost after the change.

## Approach

Use a staged, conservative CI design:

1. Add cheap workflow controls first: skip draft PRs, ignore docs/data-only changes, move Ruff to a cheap `ubuntu-latest` job, and cache Foundry and Lagoon soldeer dependencies.
2. Add a tested selective CI classifier that maps changed `eth_defi/<subsystem>` and `tests/<subsystem>` paths to a smaller pytest target list, falling back to the full suite for shared or ambiguous changes.
3. Stop pytest after the first failure with `-x` and keep workflow matrix `fail-fast` enabled so broken commits do not spend minutes running unrelated failures.
4. Use top-level workflow concurrency modelled after ApeWorX/ape so stale PR runs are cancelled before expensive jobs continue.
5. Keep heavyweight integration suite splitting as a later commit in the same PR only if the measurement gate still shows the PR cannot reach the issue's target.

This avoids the existing PR #1035 failure mode: broad dependency churn, unrelated test edits, and too many behavioural changes in one step.

## Components

- `.github/workflows/test.yml`: remains the main Python test workflow. It gets a detect job and a test job, while preserving the existing setup and pytest flags.
- `.github/workflows/lint.yml`: runs Ruff format checks separately on `ubuntu-latest`, pinned to the repo's current Ruff version and scoped to CI-owned files in this PR so unrelated historical formatting drift does not block rollout.
- `scripts/ci/classify_changes.py`: pure Python classifier with no GitHub API dependency. It reads changed paths and emits GitHub Actions outputs.
- `tests/ci/test_classify_changes.py`: unit tests for selective CI behaviour.
- `CHANGELOG.md`: one entry for CI cost reduction.

## Measurement

Before implementation and after each committed stage, run bounded GitHub Actions queries against recent `test.yml` pull request runs. The comparison uses wall-clock runtime as a proxy and multiplies by runner billing multiplier when relevant:

- `ubuntu-latest`: 1x
- `Beefy runners`: treated as the expensive constrained runner; compare wall-clock separately because its billing multiplier is organisation-specific

For each gate, record:

- number of recent pull request runs sampled
- median `Automated test suite` wall-clock runtime
- number of skipped runs expected from path filters
- whether expensive runner usage is reduced or unchanged

The PR body should include the before/after table. If a stage does not reduce cost materially, the next stage must explain why it is still needed.

## Error handling

Selective CI must be conservative. Any ambiguous change must run the full test suite:

- root-level Python files
- `pyproject.toml`, `poetry.lock`, workflow files, contract files, docs configuration
- files under `eth_defi/` without a matching `tests/<subsystem>` directory
- files under `tests/` without a matching `eth_defi/<subsystem>` package
- commit messages containing `[ci full]`
- pushes to `master`

This prevents accidental loss of coverage.

## Testing

Verification should cover:

- YAML parsing for changed workflows
- classifier unit tests
- local classifier smoke runs using artificial changed-file lists
- Ruff format for new Python code

Secret-dependent pytest suites are not required for the CI workflow change unless `.local-test.env` is available.
