## Run after merge

Migration **036-lagoon-offchain-metadata.py** is a fill-only description repair for 22 exact chain/address pairs. Maximum change: 22 rows / 43 text fields. Strada USDC requires only its summary. Missing metadata rows are reported without creation; existing non-empty text, flags, curators and historical data are preserved. A provider discrepancy or rate limit fails the batch without committing partial results.

Inspect `docker-compose.yml`, persistent mounts, the stamped image commit and the full pending migration batch before running. Build the merged image with `scripts/build-container.sh`. Stop the looped scanner and all competing metadata writers for both preview and apply. From `~/vault-scanner/web3-ethereum-defi`:

```sh
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose up -d --no-deps --force-recreate vault-scanner-looped post-scanner
```

Use the same inspected image and persistent mounts throughout; the launcher is optional for automatic image pinning/preflight. Review preview output before apply. The runner takes the lifetime guard before the writer lock; do not acquire locks inside the migration. Provider caches stay temporary in both modes. On HTTP 429, stop and retry a new preview after the reported deadline rather than repeating the burst.

The new cache writes `lagoon_metadata_chain_{id}_v2.json`; legacy
`lagoon_vaults_chain_{id}.json` maps are read only and remain readable by an older
image on rollback. Keep normal metadata backups as well: rolling back code also
restores the old unlisted-vault ingestion bug. Do not delete either cache
directory or reader state to roll back.

After restarting, normal cleaning/export/upload must publish the filled text. Check the individual and aggregate JSON and confirm target flags remain unchanged. No price backfill, reader-state reset or cache-directory deletion is required.

Manual real-provider test, 2026-11-10 UTC, public `app.lagoon.finance` API, no credentials/RPC:

```sh
source .local-test.env && RUN_LAGOON_LIVE=true timeout 180s poetry run pytest tests/lagoon/test_lagoon_unlisted_metadata.py -v -rA --tb=short
```

**Passed:** 1 test. An exact-address request for closed/unlisted Autonomous Liquidity Plus returned both descriptions, independently confirming public membership was false.

## Implementation checks

The focused cache, adapter, scanner, scheduling and migration suite passed
**200 tests, with one existing skipped case**, on 10 November 2026. This includes
the real public API check above, independent-process cache writers, unknown
listing status, nullable curator fields, shared rate-limit backoff, the
scanner-to-JSON path and actual numbered-runner preview/apply isolation.

Cleanup and grounded independent review used Claude CLI with
`claude-opus-5-5`. Findings on rollback compatibility, nullable curators,
provider throttling and reviewed private-vault notes were fixed and covered.
Final verdict: **no blocking findings; ready for handoff**. Public note wording
was checked against `.claude/docs/description-writing-style.md`; provider copy
is parsed automatically, with no new handwritten strategy claims.

Poetry/Ruff formatting and import/undefined-name checks passed for the reviewed
Python paths. Ruff's F601 duplicate-key check has an existing unrelated failure
in `eth_defi/vault/flag.py` (the repeated `0x694e47afd14a64661a04eee674fb331bcdef3737`
entry, also present in HEAD); it was left unchanged. All other selected F/I
checks and `git diff --check` passed. No Sphinx build was attempted.

No production migration, deployment or upload has been performed. These checks
do not replace reviewing the full pending production migration batch.

## Strategy refresh in the same pending batch

Migration **037-lagoon-strategy-tags.py** follows 036 and refreshes only
`_strategy_tags` for existing Lagoon rows with a maintained chain/address
classification. It uses the shared resolver, including existing curator-level
tags. Unmapped rows keep their persisted tags; missing or incompatible detection
on a classified row aborts the migration. It inserts no vaults and changes no
eligibility, descriptions, curators, flags or historical data.

The [complete strategy review](lagoon-strategy-tags-2026-11-10.md) covers 641
known deployments, with 40 new and eight updated classifications. Maintained
coverage is 111/641 overall, 66/160 in the published feed and 58/58 in the current
public catalogue. This inventory includes test and historical deployments.

Use the commands above for the **full pending batch**, reviewing the preview
counts for both 036 and 037 before applying. Do not reuse migration 008 for this
refresh. Following restart, check the exported strategy tags as well as the
filled descriptions. No price backfill is required for a tag-only refresh.

Strategy-refresh verification on 10 November 2026: the focused mapping,
numbered-runner and shared-repair suite passed **224 tests**. A separate run on
an isolated copy of the complete local metadata database previewed and applied
037: **639 Lagoon rows inspected, 111 classified, 528 unresolved, 48 rows/fields
changed**. Every changed field was `_strategy_tags`; all other row fields and
scanner block state matched, and the original metadata file remained byte-for-byte
unchanged. These are local snapshot results, not production counts.

The strategy change completed `clean-up-for-pr` and grounded independent Claude
CLI review using `claude-opus-5-5`, followed by a focused review of the fixes.
Final verdict: **approve; no remaining issues requiring a fix before merge**.
The review corrections cover the deprecated S&P500 deployment label,
denomination-asset beta, exact-deployment AMM evidence, perpetual-instrument
rationale, cross-chain negative coverage and migration/documentation wording.
Public description writing review is not applicable to this tag-only change.
Formatting, selected Ruff F/I checks and `git diff --check` passed.
Production migrations and uploads must be run separately after merge.

## Follow-up code cleanup

On 10 November 2026, a further local cleanup removed repeated metadata reads
from the description, summary and curator-name accessors, simplified curator
parsing and legacy-cache expiry calculation, and replaced migration 037's
resolver-label check with direct validation of the shared adapter priority.
Native and higher-priority adapter detections fail before staging tag updates.
The separate missing-detection error remains available for diagnosis.

Parser, refresh and accessor docstrings now describe arguments, return values
and source behaviour. The API documentation distinguishes potentially refreshing
standalone reads from the scanner's pinned row snapshot. Forced catalogue reads
still respect failed-attempt deadlines; detail reads also respect shared provider
backoff. No strategy assignments or migration scopes changed, so the pending
036/037 batch still covers this implementation; no further migration is needed.

The cleanup suite passed **364 tests, 1 existing skip**. After the final review
corrections, the affected cache, accessor and migration checks passed **56 tests**.
That final run explicitly reported `test_unlisted_detail_live` as **PASSED**,
using `RUN_LAGOON_LIVE=true` with `-rA` against the public Lagoon API, without
credentials or RPC. These runs overlap and must not be added together.

Grounded independent Claude CLI review with `claude-opus-5-5` returned
**approve, nothing blocking**. Documentation hierarchy, refresh wording,
accessor-test scope and the migration guard assertions were corrected following
that review. Ruff formatting, selected F/I checks and whitespace checks passed.
No Sphinx build was run. No manually maintained public copy changed, so the prior
writing-style review still applies. No production migration or upload was
performed during this cleanup.
