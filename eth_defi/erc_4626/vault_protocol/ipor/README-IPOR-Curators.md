# IPOR curator mapping

IPOR Fusion uses the word `atomist` for the party managing a vault strategy.
In this repository we map IPOR atomists to the shared vault curator system, so
IPOR vaults can be grouped under the same curator records as Morpho, Euler,
Lagoon, GRVT and other vault protocols.

IPOR uses the same protocol manager metadata mechanism as other ERC-4626
protocols:

| Protocol | Vault `manager_name` source | Curator YAML field |
| --- | --- | --- |
| IPOR Fusion | Atomist display name | `ipor-atomist` |
| Euler | Offchain API `entity` value | `euler-entity` |
| Morpho | Offchain API curator display name | `morpho-curator` |
| Lagoon Finance | API curator display name | `lagoon-curator` |

The curator mapping flow is:

1. IPOR vault address -> IPOR atomist display name.
2. IPOR atomist display name -> generic vault `manager_name`.
3. Exact `ipor-atomist` value -> repository curator slug.
4. Curator slug -> metadata YAML in `eth_defi/data/feeds/curators/`.

## Data sources

IPOR manager names are fetched dynamically and cached locally under the normal
eth_defi cache root:

```text
~/.tradingstrategy/cache/ipor/
```

The preferred source is the IPOR customisation API:

```text
https://api.ipor.io/fusion/vaults-customization-list
```

The frontend schema contains optional `curatorName` and `curatorLogoUrl`
fields. When `curatorName` is populated we use it as the IPOR atomist display
name.

As of 2026-06-23, production customisation rows expose the schema but do not
populate `curatorName`. Until IPOR fills that field, the fallback source is the
public IPOR Fusion frontend bundle:

```text
https://app.ipor.io/fusion
```

The bundle contains the same `atomist` values that the IPOR app displays for
configured vaults. `offchain_metadata.py` parses the bundled
`address` -> `atomist` config and uses it as an address-keyed fallback when the
customisation API has no `curatorName` for a vault.

The cache stores parsed JSON files:

- `ipor_vault_customisations.json`
- `ipor_frontend_atomists.json`

Values are IPOR atomist names as shown by the IPOR app or API. Keep the value as
IPOR publishes it, even when the spelling differs from the curator's official
name. The same value must appear in the resolved curator YAML `ipor-atomist`
field. This exact field is the primary mapping; curator name patterns are only
needed when the spelling should also be recognised by the generic fuzzy
`manager_name` fallback.

The loader is implemented in `offchain_metadata.py`:

- `fetch_ipor_vault_atomist()` performs the lazy vault accessor lookup using a
  case-insensitive address comparison. It may refresh stale IPOR caches the
  first time `IPORVault.atomist` is read, matching the accessor pattern used by
  other protocol offchain metadata modules.
- `fetch_ipor_atomist_names()` returns the fetched atomist name set for
  curator maintenance checks.

`IPORVault.atomist` in `vault.py` exposes the cached value for an IPOR vault
instance. `IPORVault.manager_name` returns the same value through the generic
ERC-4626 manager hook. During vault scanning, `eth_defi/erc_4626/scan.py`
writes `vault.manager_name` to the internal scan row field `_manager_name`.

Later, vault metric processing passes `_manager_name` to
`eth_defi.vault.curator.identify_curator()`. Curator detection first handles
protocol-level system vaults and priority vault-name matches. It then checks
exact protocol manager metadata, including `ipor-atomist`, before ordinary
vault-name fuzzy matching. This means priority branded vault names can still
win, while IPOR vaults whose names do not contain the atomist brand can resolve
from the atomist field.

## Backfilling manager names

IPOR manager-name backfill is a targeted metadata update. There is no read-time
`VaultDatabase.read()` repair step and no committed `vault_atomists.json`
overlay. Do not use `RESET_LEADS=1` for this: IPOR vaults can exist on multiple
chains, and a full historical rediscovery is unnecessarily broad for filling
one offchain metadata field.

The IPOR atomist accessor only needs the vault's chain id and address. It reads
IPOR's API/frontend metadata and local cache, not JSON-RPC state, so one script
can update all stored IPOR rows across all chains in the vault database.

Use this when existing production rows were scanned before IPOR atomist support
was added, or when IPOR changes atomist metadata and `_manager_name` values
need to be refreshed.

1. Back up the current vault metadata pickle.

```shell
cp ~/.tradingstrategy/vaults/vault-metadata-db.pickle \
   ~/.tradingstrategy/vaults/vault-metadata-db.before-ipor-manager-backfill.pickle
```

2. Optionally clear the local IPOR metadata cache to force a fresh IPOR API and
   frontend bundle read.

```shell
rm -f ~/.tradingstrategy/cache/ipor/ipor_vault_customisations.json
rm -f ~/.tradingstrategy/cache/ipor/ipor_frontend_atomists.json
```

3. Run a dry run to see which rows would be updated. Set `VAULT_DB` if the
   database is not in the default pipeline location.

```shell
poetry run python - <<'PY'
import os
from dataclasses import dataclass
from pathlib import Path

from eth_defi.erc_4626.vault_protocol.ipor.offchain_metadata import fetch_ipor_vault_atomist
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, VaultDatabase


@dataclass(slots=True)
class _Eth:
    """Small stand-in for ``web3.eth``.

    IPOR atomist lookup only needs ``chain_id``; it does not perform JSON-RPC
    reads.
    """

    chain_id: int


@dataclass(slots=True)
class _Web3:
    """Small stand-in for :py:class:`web3.Web3`."""

    eth: _Eth


vault_db_path = Path(os.environ.get("VAULT_DB", str(DEFAULT_VAULT_DATABASE))).expanduser()
dry_run = os.environ.get("DRY_RUN", "true").casefold() != "false"

db = VaultDatabase.read(vault_db_path)
updated = 0
available = 0
missing: list[tuple[int, str, str]] = []

for spec, row in db.rows.items():
    if row.get("Protocol") != "IPOR Fusion":
        continue

    manager_name = fetch_ipor_vault_atomist(_Web3(_Eth(spec.chain_id)), spec.vault_address)
    if not manager_name:
        missing.append((spec.chain_id, spec.vault_address, row.get("Name") or ""))
        continue

    available += 1
    if row.get("_manager_name") != manager_name:
        print(f"{'Would update' if dry_run else 'Updating'} {spec.chain_id}:{spec.vault_address} -> {manager_name}")
        row["_manager_name"] = manager_name
        updated += 1

print(f"IPOR rows with available manager name: {available}")
print(f"IPOR rows without available manager name: {len(missing)}")
print(f"Rows to update: {updated}")

if missing:
    print("Rows without live IPOR manager metadata:")
    for chain_id, address, name in missing[:20]:
        print(f"- {chain_id}:{address} {name}")

if updated and dry_run:
    print("Dry run only. Re-run with DRY_RUN=false to write changes.")
elif updated:
    db.write(vault_db_path)
    print(f"Wrote {vault_db_path}")
PY
```

4. If the dry run looks correct, re-run the same snippet with
   `DRY_RUN=false` to write the updated `_manager_name` values.

5. Verify that every IPOR row with live IPOR manager metadata now has
   `_manager_name` filled.

```shell
poetry run python - <<'PY'
from dataclasses import dataclass

from eth_defi.erc_4626.vault_protocol.ipor.offchain_metadata import fetch_ipor_vault_atomist
from eth_defi.vault.vaultdb import VaultDatabase


@dataclass(slots=True)
class _Eth:
    """Small stand-in for ``web3.eth``."""

    chain_id: int


@dataclass(slots=True)
class _Web3:
    """Small stand-in for :py:class:`web3.Web3`."""

    eth: _Eth


db = VaultDatabase.read()
available = 0
missing_after_backfill: list[tuple[int, str, str, str]] = []

for spec, row in db.rows.items():
    if row.get("Protocol") != "IPOR Fusion":
        continue

    manager_name = fetch_ipor_vault_atomist(_Web3(_Eth(spec.chain_id)), spec.vault_address)
    if not manager_name:
        continue

    available += 1
    if row.get("_manager_name") != manager_name:
        missing_after_backfill.append((spec.chain_id, spec.vault_address, manager_name, row.get("Name") or ""))

print(f"IPOR rows with available manager name: {available}")
print(f"Rows still missing or stale _manager_name: {len(missing_after_backfill)}")
for chain_id, address, manager_name, name in missing_after_backfill[:20]:
    print(f"- {chain_id}:{address} expected {manager_name}: {name}")

assert not missing_after_backfill
PY
```

Rows without live IPOR manager metadata are left unchanged. First check whether
such vaults appear in the current IPOR app or API metadata. Removed or
historical IPOR frontend vaults are not backfilled from static exceptions.

## Curator records

Curator metadata lives under:

```text
eth_defi/data/feeds/curators/
```

Each curator has a YAML record with a stable `feeder-id`, display `name`,
descriptions and optional feed sources such as `twitter`, `linkedin` and `rss`.
Curators that appear as IPOR atomists must also declare the exact IPOR display
name in `ipor-atomist`:

```yaml
feeder-id: tau
name: TAU
role: curator
ipor-atomist: TAU Labs
```

For IPOR atomists, use the same curator slug that the organisation already uses
elsewhere in the repository when it exists. Examples:

| IPOR atomist | Curator slug | Notes |
| --- | --- | --- |
| `TAU Labs` | `tau` | IPOR-specific display name maps to existing TAU record. |
| `Clearstar` | `clearstar-labs` | Short IPOR name maps through `CURATOR_NAME_PATTERNS`. |
| `Llama Risk` | `llama-risk` | IPOR includes a space; existing record is `LlamaRisk`. |
| `Bizantine` | `bizantine` | IPOR spelling differs from official `Byzantine Finance`. |

When the IPOR atomist spelling differs from the YAML `name`, `ipor-atomist`
is enough for exact IPOR mapping. Add an explicit pattern in
`eth_defi/vault/curator.py` only when the spelling should also work through
generic fuzzy manager-name matching:

```python
CURATOR_NAME_PATTERNS = {
    "llama-risk": ["LlamaRisk", "Llama Risk"],
    "bizantine": ["Bizantine"],
}
```

Do not create duplicate curator YAML files for spelling variants. Prefer one
canonical curator record and one or more explicit name patterns.

The same curator YAML file can carry multiple protocol manager fields when the
same organisation appears in several protocol APIs:

```yaml
feeder-id: mev-capital
name: MEV Capital
role: curator
ipor-atomist: MEV Capital
euler-entity: mev-capital
```

Protocol manager field values are stripped and compared case-insensitively.
They must be unique within the same protocol. Duplicate values fail loudly when
the curator map is loaded.

## Updating atomist mappings

When IPOR adds new vaults or changes atomists, refresh the local cache and
compare the IPOR atomist list against local curator detection.

1. Fetch the current IPOR app or API data.
2. Extract the current atomist display names from IPOR API and frontend data.
3. Run the side-by-side curator check.
4. Add missing curator YAML files or spelling patterns.
5. Add `ipor-atomist` to the resolved curator YAML.
6. Run the data-driven IPOR curator tests.

A side-by-side check can be run from the repository root:

```shell
poetry run python - <<'PY'
from eth_defi.erc_4626.vault_protocol.ipor.offchain_metadata import fetch_ipor_atomist_names
from eth_defi.vault.curator import identify_curator

atomists = sorted(fetch_ipor_atomist_names())

print("{:<20}  {:<24}  {}".format("IPOR atomist", "curator slug", "status"))
for atomist in atomists:
    slug = identify_curator(
        chain_id=1,
        vault_token_symbol="",
        vault_name="Prime HELOC Loop",
        vault_address="0x0000000000000000000000000000000000000000",
        protocol_slug="ipor-fusion",
        manager_name=atomist,
    )
    print("{:<20}  {:<24}  {}".format(atomist, str(slug), "ok" if slug else "missing"))
PY
```

This intentionally uses a neutral vault name so the result comes from the exact
`ipor-atomist` manager metadata, not from accidental vault-name matching.

## Adding a new IPOR curator

Use the repo-local `add-curator` skill when a new IPOR atomist is not already
covered by a curator YAML record or a safe spelling pattern.

For a new third-party curator:

1. Verify the organisation using official sources where possible.
2. Verify IPOR evidence for the atomist role, such as an IPOR vault page or API
   entry.
3. Create `eth_defi/data/feeds/curators/{slug}.yaml`.
4. Include `short_description` and `long_description`.
5. Add `other-links` that preserve why the organisation is treated as an IPOR
   curator.
6. Add `ipor-atomist` with the exact IPOR atomist display name.
7. Add a `CURATOR_NAME_PATTERNS` entry only when the IPOR atomist string is not
   already matched by the YAML `name`.
8. Let `tests/ipor/test_ipor_curators.py` cover the atomist through the
   dynamic manager cache and curator YAML resolver.

If the IPOR atomist is only a spelling variant of an existing curator, do not
add a new YAML file. Add `ipor-atomist` to the canonical curator YAML file.
Add the spelling to `CURATOR_NAME_PATTERNS` only if the fallback fuzzy manager
matcher also needs to recognise it.

If the atomist is the protocol itself, map it to the protocol curator slug
already used by the repository, such as `ipor` for `IPOR DAO`.

## Verification

Run focused checks after changing IPOR curator data:

```shell
poetry run python - <<'PY'
from pathlib import Path

from eth_defi.feed.sources import load_feeder_metadata

for path in sorted(Path("eth_defi/data/feeds/curators").glob("*.yaml")):
    data = load_feeder_metadata(path)
    assert data.get("short_description"), path
    assert data.get("long_description"), path

print("ok")
PY
```

```shell
poetry run ruff check eth_defi/vault/curator.py tests/vault/test_curator.py eth_defi/erc_4626/vault_protocol/ipor/offchain_metadata.py tests/ipor/test_ipor_curators.py
```

```shell
source .local-test.env && poetry run pytest tests/ipor/test_ipor_curators.py tests/vault/test_curator.py tests/feed/test_canonical_feeder.py
```
