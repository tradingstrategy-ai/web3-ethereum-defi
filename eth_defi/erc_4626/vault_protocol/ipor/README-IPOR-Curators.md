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

## Data structures

The committed IPOR atomist overlay lives at:

```text
eth_defi/data/feeds/ipor/vault_atomists.json
```

The JSON object maps a vault key to an atomist display name:

```json
{
  "1:0xdf8a0d3c90462c4c9b5a8697c119fa67cb84a874": "TAU Labs"
}
```

Keys are formatted as:

```text
{chain_id}:{lower_case_vault_address}
```

Values are IPOR atomist names as shown by the IPOR app or API. Keep the value
as IPOR publishes it, even when the spelling differs from the curator's
official name. The same value must appear in the resolved curator YAML
`ipor-atomist` field. This exact field is the primary mapping; curator name
patterns are only needed when the spelling should also be recognised by the
legacy fuzzy `manager_name` fallback.

The loader is implemented in `curators.py`:

- `IPOR_VAULT_ATOMISTS_PATH` points to the committed overlay.
- `load_ipor_vault_atomists()` returns a cached dict keyed by
  `(chain_id, lower_case_vault_address)`.
- `get_ipor_vault_atomist()` performs a case-insensitive address lookup.

`IPORVault.atomist` in `vault.py` exposes the overlay value for an IPOR vault
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
legacy fuzzy manager-name matching:

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

When IPOR adds new vaults or changes atomists, update the overlay and compare
the IPOR atomist list against local curator detection.

1. Fetch the current IPOR app or API data.
2. Extract each vault's `chainId`, `address` and `atomist`.
3. Lower-case vault addresses.
4. Update `eth_defi/data/feeds/ipor/vault_atomists.json`.
5. Run the side-by-side curator check.
6. Add missing curator YAML files or spelling patterns.
7. Add `ipor-atomist` to the resolved curator YAML.
8. Run the data-driven IPOR curator tests.

A side-by-side check can be run from the repository root:

```shell
poetry run python - <<'PY'
import json
from pathlib import Path

from eth_defi.vault.curator import identify_curator

path = Path("eth_defi/data/feeds/ipor/vault_atomists.json")
atomists = sorted(set(json.loads(path.read_text()).values()))

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
   committed overlay. The test derives atomists from `vault_atomists.json`, so
   no second hardcoded atomist list is needed.

If the IPOR atomist is only a spelling variant of an existing curator, do not
add a new YAML file. Add `ipor-atomist` to the canonical curator YAML file.
Add the spelling to `CURATOR_NAME_PATTERNS` only if the fallback fuzzy manager
matcher also needs to recognise it.

If the atomist is the protocol itself, map it to the protocol curator slug
already used by the repository, such as `ipor` for `IPOR DAO`.

## Verification

Run focused checks after changing IPOR curator data:

```shell
poetry run python -m json.tool eth_defi/data/feeds/ipor/vault_atomists.json >/tmp/ipor-vault-atomists-jsoncheck
```

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
poetry run ruff check eth_defi/vault/curator.py tests/vault/test_curator.py eth_defi/erc_4626/vault_protocol/ipor/curators.py tests/ipor/test_ipor_curators.py
```

```shell
source .local-test.env && poetry run pytest tests/ipor/test_ipor_curators.py tests/vault/test_curator.py tests/feed/test_canonical_feeder.py
```
