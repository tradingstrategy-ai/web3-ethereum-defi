# Neutral Trade vault data

This integration reads Neutral Trade's partner REST API for lead discovery,
historical backfill and ongoing observations. It uses the same native pipeline
as ApeX and Derive:

```text
Directory -> private DuckDB -> shared metadata + raw Parquet
                                    -> standard cleaner -> combined JSON
```

Neutral Trade offers market-neutral, directional and options strategies; the
filename does not imply that every vault is delta-neutral. The
[partner API guide](https://docs.neutral.trade/for-distribution-partners/api-and-sdk-and-idl)
links the [OpenAPI contract](https://api.neutral.trade/openapi.json).

## Scope and accounting

- Discover every available authenticated directory record. Only confirmed
  visible, non-private records receive detail/history reads. Partial or
  unavailable directory responses never imply retirement.
- WSOL is excluded before historical reads, even when explicitly selected.
  Hidden exclusions retain only a private minimal audit and are not printed.
- EVM records are retained privately; existing Accountable identities, fees and
  curator attribution are not overwritten. Public export currently admits
  native Solana USDC accounts only.
- The public yield feed is a catalogue cross-check. It does not establish
  complete Marketplace or Voltr coverage. The authenticated directory does not
  currently supply a verified Curated/Marketplace tier, so that field stays null.
- Canonical source PPS is denominated in the vault's asset units. The verified
  native USDC values use six-decimal PPS/equity/share fields. USD values are
  separate source metadata; denomination-based `total_assets` is never replaced
  by a USD figure. Legacy USD $1 fallbacks are not trusted market valuations.
- Native Solana fees are externalised: management/performance charges deduct
  individual shares and withdrawal fees reduce proceeds. Explicitly all-zero
  schedules, including NT Earn, are feeless. Missing rates remain unknown.
  The shared net-return fields estimate the current published base schedule;
  they do not reconstruct personal high-water marks, wallet discounts or
  historical schedule changes. Provider APY remains attributed auxiliary data.
- Legacy daily means and display/backtest histories are excluded from shared
  returns. Different canonical events at the same timestamp with conflicting
  values fail closed. Unreviewed PPS jumps above 50% quarantine that vault's
  whole shared history rather than assuming the move was a loss or a rebase.

Full-source inspection on 2026-11-12 found a share-denomination transition in
Options Market Making. Its reviewed continuous canonical interval begins on
2026-07-09; earlier data is retained privately without rescaling. CTA - Adaptive
Alpha and NT Earn have unresolved early PPS discontinuities and are currently
withheld from shared price publication. They remain discovered records.

## Curators and descriptions

The authenticated REST directory/detail contract supplies `manager` as a wallet
address. It does not supply human-readable curator names, short summaries or
long descriptions. All thirteen reviewed native USDC accounts are supplemented
through `vault_metadata.py`, using the official strategy pages' exact account
identities and the linked strategy documentation. This is reviewed editorial
metadata, not an additional field promised by the REST contract. It adds no
website scraping or external requests to normal collection.

Rows expose `_curator_slug`, `_manager_name`, `_short_description` and
`_description`; the existing JSON path publishes `curator_slug`, `curator_name`,
`short_description` and `description`. `other_data.neutral_trade.manager`
retains the original wallet, while `metadata_source` identifies the official
strategy documentation. The top-level `curators` dictionary contains the
curator profiles, including their own short and long descriptions. Published
aliases remain aliases and are not presented as verified legal names.

Stale cached JSON attribution or descriptions force one native metrics refresh,
so the low-TVL cadence cannot replay the old copy after a metadata update.
Already-corrected records keep their normal calculation cadence.

Native names such as Hyperliquid Funding Arb and Neutral Autopilot cannot
attribute their curator through fuzzy name matching. Reviewed declarations
and exact address overrides take precedence; unknown native accounts retain
empty descriptions and no inferred curator. Strategy tags are separately
reviewed by exact identity in `tags.py`; lending and listed-equity products
never inherit perpetual-futures exposure from the protocol name.

Migration `068-neutral-trade-curators-and-descriptions` refreshes these fields
in existing rows, including quarantined archival metadata. It preserves
visibility, fee rates, risk and price history. Run it after deployment through
the preview/apply commands below, without rerunning successful migrations 066
or 067. New accounts require a separate identity and strategy review before
receiving this supplemental metadata.

## Risk and fee classification

Neutral Trade has **High** technical risk. Transparency dashboards do not
remove centralised counterparty, custody or asset-freeze exposure. The
[execution model](https://docs.neutral.trade/neutral-strategy-vaults/how-they-work)
allows centralised exchanges, decentralised exchanges and DeFi protocols,
depending on strategy; it does not support a claim that every strategy avoids DEXs.

The [fee documentation](https://docs.neutral.trade/additional-info/fees-+-redemption-period)
describes elapsed-time management fees in shares and individual high-water-mark
performance charges. The [SDK fee estimator](https://github.com/neutral-trade/sdk/blob/7d8b41044e757c378d9a20eddfafa507ee2ec15c/src/extensions/fees.ts)
calculates deductions from user holdings separately from common PPS. This
establishes the externalised classification, despite the inability to replay
an individual wallet's settlement history. EVM products use different contracts
and retain the existing Accountable classification.

## Identity and storage

Solana mainnet uses the shared `eth_defi.solana.SOLANA_CHAIN_ID = 9992`
reservation, which is not an EVM chain ID. The Neutral Trade constant remains
a compatibility alias. `VaultSpec(9992, base58_account)` accepts raw native
addresses, validates their canonical 32-byte encoding and converts them before
any lowercase normalisation. Solana identities are rejected under EVM chain
IDs, and EVM/platform identifiers are rejected under the Solana reservation.

The provider-neutral key is `solana-vault-` plus the decoded account's 32 bytes
in lowercase hex. `eth_defi.solana.decode_solana_vault_address()` recovers the
exact base58 account for any provider. Program-derived accounts are supported
without an on-curve signing-key test. Existing keys and collected history are
unchanged, so no metadata or price migration is needed for this change. Exact
base58 remains in `other_data.neutral_trade.vault_address` and the Solscan link.
Native protocol/chain metadata is registered here. A consuming frontend must
recognise the new chain descriptor and use the preserved base58/explorer fields.

The default private archive is
`~/.tradingstrategy/vaults/neutral-trade-vaults.duckdb` (under the configured
pipeline directory in the scanner). Tables are unconstrained to avoid DuckDB
ART-index corruption:

| Table | Contents |
| --- | --- |
| `leads` | Current directory data, metrics, first/last seen and skip reason |
| `observations` | Original canonical ticks/snapshots, event keys, cursors' envelope provenance and fetch times |
| `history_sync` | Fixed date range, pending cursor, visited cursors, completion, successful read time and error |

A source `filelock` serialises all owners; HTTP workers never write DuckDB.
Each page and continuation commit atomically. Repeated cursors fail; rejected
expired cursors restart the same bounded range with deduplication. Corrections
replace the logical source key, while omitted rows never erase earlier data.
The API exposes no explicit retraction contract, so absence is not a tombstone.

## Credentials and commands

Supply `NEUTRAL_TRADE_API_KEY` through the private environment. Never put the key
in a command or committed file. No wallet, signing key, SDK or RPC URL is used.

```shell
source .local-test.env
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
poetry run python scripts/neutral-trade/inspect-vaults.py
```

`PYTHONPATH` makes a worktree use its own source with the shared parent Poetry
environment. An installed release does not need it. The inspector defaults to
three vaults, two pages per endpoint and two rows per page. `MAX_VAULTS`,
`MAX_PAGES`, `PAGE_SIZE` and `VAULT_IDS` optionally narrow that read-only sample.

The persistent reader uses the same collector for initial and incremental data:

```shell
poetry run python scripts/neutral-trade/vault-metrics.py
```

The looped scanner must be stopped when using the production source archive.
The standalone command checks the lifecycle guard and writes only the private
source archive; it does not publish or modify shared metadata/Parquet.

Use isolated paths for manual backfills and deliberate refreshes:

```shell
DB_PATH=/tmp/neutral-review.duckdb REPORT_PATH=/tmp/neutral-review.txt \
  poetry run python scripts/neutral-trade/vault-metrics.py
DB_PATH=/tmp/neutral-review.duckdb HISTORY_MODE=refresh \
  poetry run python scripts/neutral-trade/vault-metrics.py
```

| Optional setting | Default | Meaning |
| --- | --- | --- |
| `MAX_WORKERS` | `2` | Threaded page readers |
| `PAGE_BUDGET` | `10000` | Manual invocation page ceiling |
| `TIME_BUDGET` | `3600` | Manual invocation scheduling budget, seconds |
| `VAULT_IDS` | All | Exact API IDs to select; exclusions still apply |
| `START_DATE`, `END_DATE` | Recoverable start / today | Inclusive UTC history range on isolated paths |
| `HISTORY_MODE` | `incremental` | Resume, or `refresh` to reopen full history |
| `DB_PATH` | Pipeline archive | Private source path |
| `REPORT_PATH` | None | Optional complete local inspection report |
| `LOG_LEVEL` | `info` | Progress output |

A normal run refreshes current metrics and backfills new leads immediately.
Completed history is maintained daily with a seven-day correction overlap.
Page/time ceilings yield pending state and cursors resume next time. Every
request has bounded retries and streaming size/time checks; an in-flight socket
read may add up to its 20-second timeout. Numeric `Retry-After` is honoured when
it fits the remaining request budget. Errors never print credentials or bodies.

## Manual verification

The persistent reader prints tables from stored data, including failed/partial
coverage. Summary tables show vault IDs, chains, denominations, row counts,
range and status. Samples include the first/last five rows, the largest period
returns and legacy transitions, with PPS/NAV/share units and source methods.
Exception tables flag price changes, accounting mismatches and incomplete work.
Private identities are not printed. A successful exit is not data-quality approval.

The implementing agent or operator must inspect identity and units, gaps, flat
periods, jumps, fee basis and source timestamps. Compare representative samples
with matching API observations. Global canonical returns are not personal net
returns. Investigate every basis anomaly; do not rescale it automatically.
Record the command, UTC date, public vault IDs/range, findings and a sanitised
excerpt in the implementation PR comment before publication.

## All-chain and publication

Opt in with `SCAN_NEUTRAL_TRADE=true` and provision the server-side key. Default
cadence is four hours; override `Neutral Trade` in `SCAN_CYCLES`. Source ticks
have five-minute/100-page budgets, two HTTP workers and resumable pending state.
Use `NEUTRAL_TRADE_DB_PATH` to override the scanner archive path. This database is
registered only in the private DuckDB backup registry.

The native merge also runs through `MERGE_NEUTRAL_TRADE=true` in
`scripts/erc-4626/post-process-prices.py`. A missing source database is a logged
no-op. Merges preserve unmatched historical rows and unrelated protocols.
Revoked approvals and quarantined bases are filtered at public cleaning and
JSON publication; archival metadata and raw price rows are retained. An old
archive or provider outage never acts as an approval revocation. The JSON
export checks current approval before replaying sticky records. Exact
observation timestamps use synthetic block number zero; no hourly prices are
invented. Empty/native-only files still use the common verified Parquet writer.

## Production onboarding: run after merge

Migration `066-neutral-trade-native-backfill` freezes the thirteen reviewed
public native USDC accounts and history through 2026-11-12. Preview collects
into temporary files and stages metadata; apply backs up source/raw data,
collects through the same reader and merges under runner-owned lifecycle and
pipeline locks. No standalone production repair command is added.

Follow the [migration runbook](../../.claude/docs/migration-scripts.md), inspect
Compose and stop the persistent writers. From the production repository:

```shell
source ../vault-rpc.env && docker compose stop vault-scanner-looped post-scanner
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=true --entrypoint vault-migrate vault-scanner-oneshot run
source ../vault-rpc.env && docker compose run --rm --no-deps -e DRY_RUN=false --entrypoint vault-migrate vault-scanner-oneshot run
```

Migration `067-neutral-trade-risk-and-fee-mode` refreshes cached native risk
and fee classifications without API reads or changes to rates or price history.
Run it after deployment through the same preview/apply commands; do not rerun
an already successful 066 to refresh accessors. The runner executes all pending
numbered scripts, not just 066, 067 or 068. Review the
inventory and manual data tables first. Preserve the mounted pipeline state;
never change HOME or bootstrap an unmounted container. After inspection and
successful apply, enable the opted-in source and normal post-processing.
Deployment and production activation are not performed by this change.

## Tests and real verification

```shell
source .local-test.env && PYTHONPATH=. timeout 180 poetry run pytest tests/neutral_trade -q
source .local-test.env && PYTHONPATH=. REQUIRE_NEUTRAL_TRADE_LIVE_TESTS=true timeout 180 poetry run pytest tests/neutral_trade -m live -q
```

Missing credentials skip ordinary authenticated live tests; the requirement
switch makes a validation run fail instead. A manually dispatched GitHub job
uses secret `NEUTRAL_TRADE_API_KEY`. No secret is available to fork PR tests.
Unit tests cover cursor rollback, correction, resume, locks, exclusion, native
identity, visibility revocation, and 100,000-row file-backed ingestion. Live
tests exercise actual partner reads and isolated persistence/export.

Manual verification on 2026-11-12 used the production REST origin with a
supplied partner key, temporary source/shared files and no RPC. Directory,
metrics, historical pagination, initial backfill, repeated reads, standard
cleaning and combined JSON export succeeded. The catalogue had 39 registry
records; the public subset included 13 native USDC vaults, one WSOL vault and
one EVM USDe vault. Counts are dated observations, not test constants. The
continuity exclusions above came from manual inspection of the printed tables.

## Manual backfill and lifetime performance table

The manual analysis command collects full available approved history into an
isolated workspace and reports every public vault, including an excluded WSOL
row and EVM source-only daily closes. It does not update production state or
replace the numbered production migration:

```shell
source .local-test.env
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
poetry run python scripts/neutral-trade/backfill-vaults.py
```

`OUTPUT_DIR` defaults to `logs/neutral-trade-backfill`; optional `MAX_WORKERS=2`,
`PAGE_BUDGET=10000` and `TIME_BUDGET=3600` bound work. Re-run the same command to
resume incomplete cursors. Production pipeline destinations are refused.
Outputs are `source.duckdb`, `performance.csv`, `performance.md` and the detailed
persisted-data `verification.txt`. The CSV stores CAGR as a decimal fraction.

Age is days since the provider's first live tick, or the first retained canonical
observation when that timestamp is unavailable. Performance uses only retained
live-day canonical data, with the reviewed Options Market Making basis cutoff.
It is not necessarily the full history since launch. The common vault analytics
module calculates lifetime CAGR and daily Sharpe (zero risk-free rate), applying
its 30-day CAGR and 14-day Sharpe minimum-history rules. This manual report
deliberately uses gross canonical returns. Published net-return fields separately
estimate the base fee schedule; realised personal net returns remain unknown.
EVM uses final daily closes to avoid ambiguous intrablock ticks.

Manual inspection also flags unexplained PPS moves above 5% while reported
asset equity changes less than 2%. These are conservative review flags, not
proof of a rebase or an automated price repair; performance stays unavailable
until investigated. This report check is additional to the shared exporter's
continuity quarantine. A successful backfill means collection completed, not
that every source series passed economic validation.
