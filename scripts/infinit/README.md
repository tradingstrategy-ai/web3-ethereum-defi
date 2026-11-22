# Infinit historical migration

The [legacy Infinit repair](migrate-infinit.py) recovered Arbitrum discovery, metadata and share-price history. It used the dense timestamp cache to limit historical reads to the contiguous range after deployment. The [protocol guide](../../docs/source/vaults/infinit/index.rst#migration) explains source coverage and recovery. For a new repair, port the required operation into a [numbered migration](../../.claude/docs/migration-scripts.md#historical-price-backfills).

## Validation

On 2026-10-26, the consolidated script passed dry-run, apply and repeated-apply
rehearsals on isolated production copies. Its automatic cached boundary was
505781464 (exclusive): 5,969 source observations, 1,493 raw rows and 874 cleaned
rows. Price values were unchanged on repeated apply; unrelated rows, context,
discovery cursors, reader state and the legacy vault were preserved. All 112
focused protocol, history, migration and curator tests passed.
