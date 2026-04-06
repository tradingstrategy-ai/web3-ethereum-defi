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
feed collection to another feeder file.  Alias files contain only identity
metadata (feeder-id, name, role) and produce no tracked sources.  See the
"Canonical feeder aliases" section in
[`eth_defi/feed/README-feed.md`](../../feed/README-feed.md) for details.

For the schema, collection behaviour, and configuration details, see
[`eth_defi/feed/README-feed.md`](../../feed/README-feed.md).
