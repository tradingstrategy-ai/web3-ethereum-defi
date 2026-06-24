# Sticky vault export state plan

## Goal

Make `scripts/erc-4626/vault-analysis-json.py` append-biased.

Once a vault passes the production `MIN_TVL` peak TVL filter, it should stay in
the JSON export even if later metrics are stale, temporarily missing, below the
current threshold, or affected by transient metadata gaps.

Manual operator suppression is not part of v1. The only removal paths are narrow
structural safety cases:

- the stored fallback record cannot identify the vault safely;
- the current or stored record has the exact blacklisted risk label.

## State file

Persist sticky history next to the pipeline data files.

Default path:

`<data_dir>/vault-export-state.json`

`VAULT_EXPORT_STATE_PATH` can override the path when an operator explicitly
wants to place the state file elsewhere.

Corrupt state must abort the sticky export. Do not reset corrupt state to an
empty file, because the state is the qualification history.

## Export algorithm

1. If `DISABLE_STICKY_VAULT_EXPORT=true`, bypass state loading, sticky
   annotations, structural suppression, and state writes.
2. Load the single shared state file or create an empty state.
3. Calculate lifetime metrics as today.
4. Build canonical state keys as `{chain_id}-{lowercase_address}` from the
   exported row identity.
5. Current rows passing `peak_nav >= MIN_TVL` qualify the vault forever, unless
   the row has exact `Blacklisted` risk.
6. If a current row is missing or has a null required metadata value such as
   `name` or `protocol_slug`, do not replace `last_exported_record`. For a
   previously sticky vault, replay the stored fallback record instead.
   `curator_slug` is nullable and does not make a row unsafe by itself.
7. If a sticky vault has no current row, replay `last_exported_record` with
   stale annotations.
8. If the fallback record is empty, has invalid identity fields, or has exact
   `Blacklisted` risk, mark the state entry `status="suppressed"` with a
   structural `suppression_reason`. A clean, non-blacklisted current row that
   later passes the export filter can reactivate a structurally suppressed entry.
9. Do not expire sticky vaults automatically. Staleness only adds annotations
   and operator counters.
10. Inject sticky rows before deriving top-level `core3_protocols` and
    `curators`.
11. If a stale sticky row references a protocol or curator slug that no longer
    resolves, keep the vault row and emit missing-slug counters.
12. Validate JSON serialisability, atomically write the output JSON, then
    atomically write the state file.

## Risk handling

`export_lifetime_row()` serialises `VaultTechnicalRisk.blacklisted` as
`"Blacklisted"`. Match the enum, its numeric value, and the exported
`"Blacklisted"` label.

Blacklisted current rows are not exported or recorded as active sticky entries.
Blacklisted fallback rows are not replayed.

## Timestamp handling

Use `native_datetime_utc_now()` for current time.

When comparing current row timestamps:

1. Parse the value as a Pandas timestamp.
2. If it is timezone-aware, convert to UTC.
3. Drop timezone information after UTC conversion.
4. Compare as naive UTC.

This avoids aware-vs-naive `TypeError` and avoids wall-clock shifts from
dropping timezone information too early.

## Operator controls

- `VAULT_EXPORT_STATE_PATH`: optional explicit state path.
- `DISABLE_STICKY_VAULT_EXPORT=true`: emergency bypass for sticky state.
- `STICKY_STALE_WARNING_AGE_DAYS`: stale warning threshold, default 14.

There is intentionally no manual vault suppression environment variable in v1.

## Backups

`eth_defi.vault.data_file_export.get_data_file_paths()` must include
`vault-export-state.json` so daily R2 backups include sticky qualification
history.

`scan-vaults-all-chains.py` calls `eth_defi.vault.scan_all_chains.main()`,
which passes the pipeline data directory into `run_post_processing()` and
`export_top_vaults_json()`. Because `vault-analysis-json.py` resolves the
default state path from that `data_dir`, the all-chains production path uses the
same shared state file.

## Tests

Focused tests should cover:

- first qualification creates sticky state;
- missing current metrics replay fallback;
- structurally unsafe current rows replay fallback;
- blacklisted current rows are suppressed;
- blacklisted fallback rows are suppressed using the real `"Blacklisted"` label;
- all output filenames resolve to the same default state file;
- timezone-aware timestamps are converted to UTC before comparison;
- invalid fallback records are structurally suppressed;
- sticky state files are included in data-file backups.
