# Vault lead migrations

Vault lead discovery is incremental. The normal scanner resumes from each
chain's recorded cursor and only identifies events emitted after that point.

When an integration adds a discovery event or a non-ERC-4626 vault adapter,
historical leads for that integration must be recovered through a dedicated,
generated migration script. These scripts live under
`scripts/<protocol>/backfill-history.py` and follow the Midas backfill pattern:

- define the reviewed protocol address registry and deployment blocks;
- upsert only those protocol leads and their metadata rows;
- reset reader state only for the selected vault IDs;
- rewrite raw and cleaned price data only for vaults with a supported historical
  price reader.

The integration workflow generates the migration script together with the
adapter. It is the required replacement for whole-chain lead rediscovery and
must be safe to run against an existing production vault database.

`RESET_LEADS` has been removed. Setting it for `scan-vaults.py` is an error.
The scanner must never restart discovery from block 1, because that can refresh
unrelated metadata and makes a targeted protocol migration depend on every
historical discovery adapter.
