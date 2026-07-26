# Vault lead migrations

Vault lead discovery is normally cached for seven days per chain. While the
matching `lead-discovery-state-{chain_id}.json` cache is fresh, the all-chain
scanner reuses the saved metadata database and skips the costly lead-discovery
refresh. A cache expiry, a lead-detection configuration change, or
`FORCE_LEAD_DISCOVERY=true` performs a complete HyperSync discovery from block
1 and refreshes lead metadata. This cache refresh does not reset price data or
reader states.

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
Use the bounded all-chain cache refresh only for the scanner's automatic
configuration and expiry policy; protocol migrations remain targeted so they
can update their reviewed leads without changing unrelated vault histories.
