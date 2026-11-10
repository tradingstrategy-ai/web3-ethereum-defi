# Lagoon offchain metadata coverage plan

## Objective and scope

Automatically retrieve available descriptions and curator metadata for known
Lagoon deployments, including vaults omitted from Lagoon’s public catalogue.
Keep metadata availability independent from our public-listing eligibility.
Preserve previously fetched text when a vault closes, disappears from the
catalogue or encounters a temporary provider failure.

This document is an implementation plan, not an applied repair. Do not change
production, publish handwritten descriptions, add strategy tags or broaden the
private-vault allowlist as part of writing this plan. Those editorial changes
remain separate from fixing automatic metadata ingestion.

## Evidence and current behaviour

The [inventory](../../protocol-research/lagoon-missing-descriptions-2026-11-10.md)
records 119 published Lagoon entries missing both descriptions in the export
from 2026-11-10T13:34:48Z. The [research comment](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1756#issuecomment-6769613709)
records the subsequent exact-address checks: 21 of those entries have both
fields in the API, Strada USDC has only a short summary, 94 successful responses
have neither field, and three return HTTP 404. These are dated observations,
not permanent API assertions or expected production migration counts.

Code inspected before authoring:

- `eth_defi/erc_4626/vault_protocol/lagoon/offchain_metadata.py` enumerates the
  paginated listing plus the reviewed private allowlist, fetches those details,
  and replaces the chain JSON cache. A requested address outside the cache
  simply returns `None`. Successful refreshes can drop formerly listed entries.
- That module already has daily disk/process expiry, stale fallback on listing
  failures and detail failures for retained addresses, writer locking and atomic
  JSON writes. Preserve these safeguards rather than rewriting unrelated code.
- `eth_defi/erc_4626/vault_protocol/lagoon/vault.py` exposes the result through
  a `cached_property`. Descriptions and manager names use it, while flags and
  notes interpret its absence as missing from the frontend. Merely adding a
  fallback detail fetch would therefore silently change eligibility.
- `eth_defi/erc_4626/scan.py` persists normalised descriptions and manager
  metadata. Fixing the HTTP lookup alone does not refresh previously persisted
  rows, and price-only scanning does not guarantee metadata access.
- `eth_defi/vault/scan_all_chains.py` also has a lead-discovery/metadata cadence;
  its refresh receipt can skip a metadata phase. Cache expiry by itself is not
  a scheduling mechanism.
- Existing coverage is in `tests/erc_4626/test_lagoon_metadata_cache.py`,
  `tests/erc_4626/vault_protocol/test_lagoon_private_vault_allowlist.py`,
  `tests/erc_4626/vault_protocol/test_lagoon_descriptions.py` and
  `tests/lagoon/test_lagoon_metadata.py`.

Lagoon listing membership, detail `isVisible`, and detail `state.state` are
separate observations. Closed Autonomous Liquidity Plus and MuDigital Tulipa
USDC have detail text and `isVisible=true` but are absent from the listing.
Do not infer private status or illegitimacy from omission alone.

## Behavioural contract

| Situation | Metadata outcome | Eligibility outcome |
| --- | --- | --- |
| Listed vault | Refresh exact-address details when due | Existing public eligibility rules |
| Unlisted known vault, successful detail | Return and cache its descriptions and curators | Remains unreviewed unless already allowlisted |
| Reviewed private deployment | Fetch details even outside listing | Existing exact chain/address exception |
| Formerly listed vault | Keep detail history and refresh it independently | Current catalogue/allowlist policy, not stale detail presence |
| Closed vault with text | Retain descriptive metadata | No implicit reopening or removal of flags |
| Confirmed 404 with no previous detail | Cache a bounded negative result | No new approval |
| 429, timeout, 5xx or malformed response | Keep last good metadata, defer retry | No new approval or transient reclassification |
| Empty successful description fields | Preserve the provider’s empty values as current data | No invented strategy copy |

An explicitly emptied provider field is a current source observation, unlike
a failed request. Keep that distinction in the cache, but never automatically
delete non-empty persisted description text: retain it and log a concise
WARNING naming the vault, field and retained-text age. An editorial deletion
requires a separate reviewed repair. The one-off repair below is fill-only.

## Implementation steps

### 1. Separate catalogue membership from detail storage

Introduce a small versioned chain-cache envelope in the existing Lagoon cache
module. Store the last successful public address set and its fetch time
separately from per-address detail records. Each detail record needs its last
successful payload/time and the last attempt outcome/retry deadline. Use plain
serialisable fields, naive UTC times, and existing cache paths and locks; do
not introduce a database or a generic cache framework.

Keep `fetch_lagoon_vaults_for_chain()` returning the existing address-to-metadata
mapping to callers. A successful listing refresh replaces only catalogue
membership, not all detail payloads. Bulk refresh targets are the public set
and allowlisted addresses when due. Retained unlisted addresses refresh only
on a requested lookup, as specified in step 2. A per-vault
lookup can add its requested address without waiting for listing success.
Never use an API response to create an onchain vault discovery record.

Read legacy bare address-map JSON without losing a single cached payload.
The legacy map was built only from listing plus allowlist. Seed the previous
listing-membership snapshot from its keys excluding current allowlisted
addresses, using the old file timestamp and marking legacy provenance. This
preserves previous eligibility if the first post-upgrade listing fetch fails;
it is a compatibility snapshot, not evidence of current listing. Allowlisted
addresses retain their independent exception. Only validated legacy maps may
seed this snapshot; new arbitrary-address detail records must never do so.
Validate the old shape, then atomically upgrade under the same writer lock.
Invalid JSON and unsupported versions fail with diagnostics, never reset to an empty cache.
Use separate catalogue/detail freshness clocks: fetching one detail must not
make every other record or the catalogue look fresh. In-memory state must
observe expired records and changes made by another process: check the atomic
file identity/mtime/size before reparsing, rather than reload JSON on each
accessor read.

### 2. Fetch requested unlisted details with bounded retries

Refactor the internal request result to distinguish successful validated detail,
confirmed HTTP 404, and retryable failure. A successful body must identify the
requested chain and lowercase address before accepting any payload. Treat a
wrong identity or unexpected body shape as failure; do not cache an empty or
cross-chain success. The audited real responses include `address` and
`chain.id`; confirm those fields again in the live integration test before
requiring them in validation. Update fixtures to match the verified shape.

Use daily expiry for successful non-empty active details, seven-day expiry
for successful-empty or closed details, daily 404 retry, and the existing
one-hour failed-refresh delay by default. A successful-empty record can thus
acquire newly supplied text within seven days; document this longer interval.
Respect bounded `Retry-After` on 429; do not
sleep inside the adapter or retry a burst repeatedly. Existing stale detail
survives both 404 and transient errors, with last-attempt status distinct from
last-success time. A later successful response recovers normally. Missing
entries receive negative/retry records so a scanner cannot hammer the API.

Use an in-process lock keyed by cache path/chain/address to coalesce concurrent
same-address lookups. For cross-process writes, take `wait_other_writers` only
for a short read/merge/write section; perform HTTP calls outside that lock.
Recheck freshness before issuing a request, and merge the latest disk state
after it completes. Accept a rare duplicate HTTP request across processes
rather than holding a chain writer lock for a network sweep. Use an attempt
start/version token so an older response cannot overwrite a newer result.
Tests must cover distinct-address merge, same-process single fetch and
out-of-order cross-process completions. Keep network timeouts finite.
Do not mark an entire chain freshly fetched because one requested address succeeded.

Avoid unbounded repeated sweeps: the chain catalogue refresh happens once per
its TTL. Do not sweep every historical detail on every listing refresh:
refresh retained unlisted details on demand when a known vault is requested
by a due metadata phase or the scoped migration. Remove unused negative-only
records after 30 days; never prune successful historical payloads this way.
If bulk work is parallelised, use bounded joblib threading with `max_workers`/`MAX_WORKERS`, consistent with
repository rules. No async pipeline or public arbitrary-address crawling.

### 3. Preserve eligibility and make status uncertainty explicit

Provide a chain/address-aware public-membership lookup independent of detail
presence, with listed, unlisted and unknown results. `isVisible` and closed
state remain provider context rather than replacements for membership.

Update `LagoonVault.get_flags()` and `get_notes()` together. Preserve superclass
manual flags/notes, the existing allowlist and activity exceptions. Successful
unlisted detail must not remove `unofficial`; a cached old detail must not prove
current membership. A temporary listing failure keeps the last successful
membership decision. When there is no successful membership snapshot, use the
conservative unapproved outcome without claiming confirmed catalogue absence.
Do not weaken exclusions for the two deliberately excluded private addresses.

Keep the existing description accessors and normalisation helpers. Replace or
invalidate the per-instance `lagoon_metadata` cached property so a long-lived
adapter can observe shared-cache expiry, including recovery from an initial
miss. Capture a single metadata-and-membership snapshot at the start of each
row construction; all flags, notes, descriptions and manager accessors for that row
use it. A later scan/explicit access can refresh normally, so TTL expiry
mid-row cannot mix old and new decisions.
Route manager-name reads through the same detail payload. Preserve the current
curator resolver contract; changing single-versus-multiple curator display or
assigning new curator identities requires separate reviewed work.

Generated missing-frontend notes must describe catalogue membership accurately,
not claim the API has no record. Preserve manually maintained notes. Review any
changed public note against the description style guide. Handle both `None`
and empty descriptions when deciding whether the private no-description note
applies. Do not manufacture a strategy from the vault name.

### 4. Prove the scanner reaches the new path

Trace lead discovery, scheduled metadata refresh, pending metadata retries and
price-only cycles with the changed adapter. Establish the actual maximum
metadata refresh interval and document it. Reuse the existing metadata phase
and its force-refresh mechanism; do not add a second unattended writer.

Add an integration-level regression that an already discovered, unlisted Lagoon
vault receives new API text during the next due metadata refresh without a new
discovery event, chain rescan or manual allowlisting. If existing scheduling
skips that row permanently, make the smallest targeted scheduling fix and test
the selection rule. Exercise scanner row construction and JSON export, not
only the adapter property.

Define a Lagoon-only persisted-row merge at the existing scanner merge point:
validated non-empty source text may update a description field; failure, 404,
no prior cache payload or a successful empty field cannot replace an existing
non-empty persisted value. Carry the source outcome to this merge instead of
inferring it from accessor `None`. Log retained-text cases with chain/address,
field, reason and last-success age if known; use “age unknown” for old rows.
Keep other adapters’ merge semantics unchanged. Test a repaired persisted row
with an empty production cache followed by 404, transient failure and an
explicit provider emptying: the repaired descriptions must survive each.
Verify flags, manual notes and prices stay within scope.

### 5. Refresh persisted descriptions through a numbered migration

Allocate the next unused `migration/scripts/NNN-lagoon-offchain-metadata.py`
when implementation begins; do not reserve a number now or edit successful
initial scripts. Use `.claude/docs/migration-scripts.md` and the existing
`MigrationContext` API. Freeze the reviewed 22 deployment pairs from the audit
as explicit scope after checking their source identities again. Freeze an
expected field set per target: 21 require both fields, Strada requires only
the short field and its long field remains unmodified.

The migration is required for this persisted-accessor change by repository
policy, and gives a bounded audited repair even when historical/unofficial
rows are infrequently scheduled. It reuses the same parser/merge helpers as
normal scanning rather than implementing a second source of business rules.

Fetch only those exact deployments into a `TemporaryDirectory` cache in both
preview and apply, bypassing production cache paths and catalogue gating.
Stage a fill-only transformation of `_description` and `_short_description`
on existing Lagoon rows in `context.metadata()`. Use the same parser and
normalisation as normal scans. Preserve non-empty existing text and report
conflicts, missing targets, blank provider fields and request outcomes.
Validate all targets before committing staged changes. A transient provider or
identity failure must fail the migration without recording a successful repair
of an incomplete batch; a new confirmed 404 or empty **required** field is an
explicit review discrepancy requiring resolution before marking the batch done. Strada’s
expected absent long field is not an error.

Pace this small batch sequentially (default one request per second), reporting
progress throughout. On 429, record the redacted response status and
`Retry-After`, then abort the batch without repeated requests. Retry the
preview as a new run only after that deadline (default one hour if absent),
then review before apply; do not add an unbounded in-adapter wait. This
fail-and-retry policy deliberately favours a visible incomplete attempt over
quiet partial success, and works with the runner’s failed-attempt handling.

Do not instantiate an adapter requiring RPC just to copy API strings. Do not
create rows or change flags, fees, names, notes, curator assignments, tags,
prices, leads, reader state or timestamp caches in this repair. Curator and
generated-note repairs can follow in separately scoped numbered scripts after
their resolver semantics are reviewed. Call `context.report()` and
`context.commit_metadata()`; acquire no extra migration locks. Verify a second
application makes no metadata changes, while reporting that live API content
can legitimately change between preview and apply.

This correction adds descriptive text without changing eligibility or price
collection, so it needs no historical price backfill. A later change that
admits a vault must separately assess early price gaps, especially Monad.

## Validation and acceptance

Add focused tests for these behaviours rather than mirroring helper internals:

- Unlisted exact-address success, case normalisation and cross-chain isolation;
  API chain/address mismatch rejected; both and short-only field cases.
- Listing success/failure/empty responses and allowlisted-only chains; retained
  details survive disappearance and are refreshed despite listing outages.
- Separate positive, 404 and transient TTLs; 429 retry deadline; stale fallback;
  recovery after a miss; old JSON cache upgrade and corrupt cache hard failure.
- Legacy upgrade followed by a listing outage preserves existing eligibility.
- Same-process same-address single fetch, distinct-address merge and older
  responses completing last; no cross-process duplicate-request guarantee.
  Independent clocks; long-lived adapter recovery; process cache observes disk updates.
- Eligibility remains independent: unlisted detail and retained closed detail
  do not clear `unofficial`, reviewed allowlist remains exact, manual flags and
  notes survive, unknown listing status does not assert confirmed absence,
  and all per-row accessors share one metadata/membership snapshot.
- Scanner-to-export propagation on a due metadata cycle for an existing vault;
  unchanged eligibility and no extra price scans.
- Migration preview writes no persistent metadata/cache/state; fill-only,
  idempotent apply; missing/mismatched rows and incomplete provider batch fail
  clearly; Strada-style short-only scope succeeds; 429 aborts without commit
  and exposes a safe retry deadline; unrelated fields remain byte/value
  equivalent where appropriate. Repaired text survives subsequent scanner
  misses, 404 and successful empty fields even with a fresh production cache.

Add a minimal real API integration test using an exact reviewed unlisted
address, temporary cache and no RPC. Verify successful HTTP response, matching
identity and non-empty expected field, not volatile performance values. Also
check public membership independently. Keep deterministic tests authoritative
for closure/listing changes. Follow the existing CI restriction for Lagoon API
failures, with a guarded manual command if CI cannot reliably run it. Record
the exact real-test command, UTC date, provider and outcome in the future PR.
The previous research requests do not replace a test of the implementation.

Run focused pytest through `source .local-test.env && PYTHONPATH="$PWD" poetry
run pytest ...`, using a three-minute command timeout; copy the ignored env
file from the main checkout if absent. Format changed Python paths explicitly
with Poetry/Ruff, run `git diff --check`, and do not build Sphinx. Run
`clean-up-for-pr` once after the implementation is complete and obtain the
required independent review. Public wording review is N/A for this engineering
plan, but required for changed generated notes or later handwritten copy.

Acceptance: API-backed text for a known unlisted vault reaches persisted
metadata and JSON on a due metadata cycle; closing/delisting does not discard
its descriptive history; requests are bounded; transient outages do not erase
text or newly approve vaults; the scoped migration can populate the audited
missing fields without changing listing decisions or historical price data.

## Production handoff for the future implementation

Add a Run after merge PR comment with the new migration and expected scope.
Inspect the production Compose configuration, service entrypoints, pending
migration batch, stamped image and persistent mounts before maintenance. Stop
`vault-scanner-looped` and competing writers before both preview and apply.
Use the direct Compose `vault-scanner-oneshot` service with entrypoint
`vault-migrate`, preserving the lifetime-guard-before-writer-lock order. Follow
the migration runbook rather than old standalone repair scripts.

Keep the same inspected image and mounted `.tradingstrategy` state throughout.
Preview must not modify provider caches. Apply after reviewing proposed counts,
then restart the inspected scanner image and run normal cleaning/export/upload.
Confirm target text in individual vault JSON and the aggregate feed, confirm
flags are unchanged for the migration targets, and compare before/after missing
counts. If application fails, inspect the migration journal and backup; do not
reset cache/history directories or reader state. Do not push, update the PR
or perform production maintenance merely because this plan exists.

## Review record

Reviewed on 10 November 2026 through Claude CLI with the exact model
`claude-opus-5-5`, successfully smoke-tested before review. Both passes supplied
the complete plan inline with tools disabled, following the repository’s plan
review guidance after the primary agent inspected the relevant code.

The first pass found blocking risks in legacy-cache eligibility during an
outage and persisted-text deletion after migration. Both were addressed.
Additional revisions specify expected migration fields, bounded 429 handling,
short writer-lock scope, request volume, old-response ordering, process-cache
reload checks and one consistent snapshot per scanner row.

The final pass returned **“No blocking findings”** and **“the plan is approved
for implementation.”** Final editorial cleanup clarified the on-demand-only
refresh of retained unlisted addresses and wrapped long prose lines. Checked
relative research links, repository English and `git diff --check`; no code,
production data or public website copy changed. No tests or Sphinx build were
run for this plan-only change. Implementation still requires its own focused
checks, real-provider test, cleanup review and production migration handoff.

## Implementation status

Implemented locally on 10 November 2026, with migration
`059-lagoon-offchain-metadata.py`. Grounded Claude Opus 5.5 review and focused
regressions are complete; final verdict had no blocking findings. Review-driven
adjustments put the v2 envelope in a separate file for rollback compatibility,
keep legacy maps read-only, accept optional curator identities and defer chain
detail requests after provider rate limits. The scanner releases each pinned
snapshot in `finally`. The final suite passed 200 tests with one skipped case.

The [deployment runbook](../../protocol-research/lagoon-metadata-repair-runbook-2026-11-10.md)
records the manual real-provider result, pending production operation and the
unrelated pre-existing Ruff F601 finding. Implementation does not broaden the
private allowlist or add editorial strategy descriptions/tags. No code has been
pushed or production data changed by this implementation task.
