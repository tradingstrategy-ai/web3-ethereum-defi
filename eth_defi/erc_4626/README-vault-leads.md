# Vault lead migrations

Vault lead discovery is normally cached for seven days per chain. While the
matching `lead-discovery-state-{chain_id}.json` cache is fresh, the all-chain
scanner reuses the saved metadata database and skips the costly lead-discovery
refresh. A cache expiry, a lead-detection configuration change, or
`FORCE_LEAD_DISCOVERY=true` refreshes classifications and metadata for every
persisted lead. It resumes event discovery from the database cursor, so it
does not replay the chain from block 1. This cache refresh does not reset price
data or reader states.

The first discovery for a chain still requires HyperSync. The scanner refuses a
genesis-to-head JSON-RPC event read, because it is too costly and unreliable for
production providers.

When an integration adds a discovery event or a non-ERC-4626 vault adapter,
recover any required historical leads through the
[numbered migration system](../../.claude/docs/migration-scripts.md). Add
`migration/scripts/NNN-name.py` with a `migrate(context)` entry point and run it
through `vault-migrate`; production uses the guarded host launcher.

Standalone `scripts/<protocol>/backfill-history.py` and
`migrate-<protocol>-vaults.py` entrypoints are **legacy migration format**.
Do not generate, copy or run that format for lead repairs anymore. Reuse the
underlying discovery and historical-reader helpers from a numbered migration.

- Freeze the reviewed protocol address registry and deployment boundaries.
- Upsert only the reviewed leads and their metadata rows.
- Preserve reader state unless a reviewed state change is explicitly required;
  never reset it merely to force discovery or a price refresh.
- Add address-scoped historical writes only where required and supported,
  preserving unrelated raw/cleaned prices and timestamp caches.
- Respect the context's preview mode and report committed files and progress.
  Both preview and apply require daemon exclusion; helpers must not reacquire
  the runner's lifecycle or pipeline writer locks.

The integration includes this numbered repair alongside its adapter. It must
be safe against existing data and must not trigger whole-chain rediscovery.

The production launcher disables automatic price scanning. A required numbered
backfill must invoke its fixed-scope historical writer explicitly, verify its
dense timestamp cache and price-write postconditions, and fail if the work is
skipped or unavailable. Do not call a legacy wrapper that silently skips prices
because `SCAN_PRICES` or `UPSHIFT_SCAN_PRICES` is false; follow the guide's
historical-backfill requirements.

`RESET_LEADS` has been removed. Setting it for `scan-vaults.py` is an error.
Use the bounded all-chain cache refresh only for the scanner's automatic
configuration and expiry policy; protocol migrations remain targeted so they
can update their reviewed leads without changing unrelated vault histories.
