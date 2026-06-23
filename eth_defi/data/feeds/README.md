# Feed data

This folder contains feeder YAML files used by the
[`eth_defi.feed`](../../feed/README-feed.md) submodule.

Current subfolders:

- `protocols/`
- `curators/`
- `stablecoins/`
- `vaults/`

The feed loader scans all YAML files recursively under this folder, so we can
add more subfolders later without changing the loader contract.

Some YAML files are **aliases** — they set `canonical-feeder-id` to delegate
feed collection to another feeder file. Alias files contain identity metadata
and other non-source metadata, such as descriptions or protocol manager
metadata, but produce no tracked sources. See the "Canonical feeder aliases"
section in [`eth_defi/feed/README-feed.md`](../../feed/README-feed.md) for
details.

Curator YAML files can also carry protocol-specific manager metadata used by
vault curator detection:

- `ipor-atomist`: IPOR Fusion atomist display name.
- `euler-entity`: Euler offchain API `entity` value.
- `morpho-curator`: Morpho offchain API curator display name.
- `lagoon-curator`: Lagoon API curator display name.

These fields are not feed sources. They map protocol-native manager names to
the repository's canonical curator records so vault scans can resolve curators
from `VaultBase.manager_name` without relying on vault-name fuzzy matching.
Use the exact value exposed by the protocol API or app, and keep spelling
variants on the canonical curator YAML instead of creating duplicate curator
records.

For the schema, collection behaviour, and configuration details, see
[`eth_defi/feed/README-feed.md`](../../feed/README-feed.md).
