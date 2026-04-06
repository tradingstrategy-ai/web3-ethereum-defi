# Data directory

Static data assets bundled with the `eth_defi` package: protocol metadata, stablecoin catalogues,
curator profiles, feed source configuration, and branded logos.

No Python files live here — all loading logic is in the source modules listed below.

## Directory layout

```
eth_defi/data/
├── curators.md                          Human-readable curator reference
├── feeds/                               Feed source YAML files
│   ├── curators/      (38 YAML)         Curator social/RSS sources
│   ├── protocols/     (70 YAML)         Vault protocol social/RSS sources
│   └── stablecoins/  (175 YAML)         Stablecoin social/RSS sources
├── vaults/                              Vault protocol metadata and logos
│   ├── metadata/      (71 YAML)         Protocol descriptions, links, fees
│   ├── original_logos/ (70 dirs)        Raw downloaded brand assets
│   └── formatted_logos/ (70 dirs)       Standardised 256x256 PNG logos
└── stablecoins/                         Stablecoin metadata and logos
    ├── *.yaml         (189 YAML)        Stablecoin descriptions, links, contract addresses
    ├── original_logos/ (187 dirs)       Raw downloaded brand assets
    └── formatted_logos/ (187 dirs)      Standardised 256x256 PNG logos
```

## Folders in detail

### `feeds/`

YAML files describing social media and RSS feed sources for protocols, curators, and stablecoins.
Used by the post-scanning pipeline to discover and collect news content.

Each YAML file follows a common schema with fields like `feeder-id`, `name`, `role`, `website`,
`twitter`, `linkedin`, and `rss`. See [`feeds/README.md`](feeds/README.md) and
[`eth_defi/feed/README-feed.md`](../feed/README-feed.md) for the full schema reference.

Progress on feed YAML creation is tracked in [`feeds/tracking.md`](feeds/tracking.md).

**Source modules that read/write this data:**

| Module | Role |
|--------|------|
| [`eth_defi/feed/sources.py`](../feed/sources.py) | Loads all feeder YAML files; also **writes** status flags (dead RSS, disabled LinkedIn, unknown Twitter) back into the YAML files |
| [`eth_defi/feed/scanner.py`](../feed/scanner.py) | Consumes loaded feed sources to scan and collect posts |
| [`eth_defi/vault/curator.py`](../vault/curator.py) | Reads `feeds/curators/` YAML to build a curator lookup map |

### `vaults/`

Vault protocol metadata and branded logos. Each protocol is identified by a slug
(lowercase, dashes for spaces — e.g. `lagoon-finance`).

See [`vaults/README.md`](vaults/README.md) for the YAML schema and logo conventions.

- `metadata/` — One YAML per protocol with `name`, `slug`, `short_description`,
  `long_description`, `fee_description`, `links`, and `example_smart_contracts`.
- `original_logos/` — Raw brand assets (SVG, PNG) with a `README.md` per protocol documenting the source URL.
- `formatted_logos/` — Standardised 256x256 px PNG files: `light.png` (dark icon) and/or `dark.png` (light icon).

**Source modules that read this data:**

| Module | Role |
|--------|------|
| [`eth_defi/vault/protocol_metadata.py`](../vault/protocol_metadata.py) | Reads YAML metadata and formatted logos; builds JSON index; uploads to R2 |
| [`scripts/erc-4626/export-protocol-metadata.py`](../../scripts/erc-4626/export-protocol-metadata.py) | Orchestrates metadata + logo export to Cloudflare R2 storage |
| [`scripts/logos/post-process-logo.py`](../../scripts/logos/post-process-logo.py) | Converts original logos into formatted 256x256 PNG files |

### `stablecoins/`

Stablecoin metadata catalogue. YAML files sit at the directory root (one per stablecoin asset).

Each YAML contains `symbol`, `name`, `slug`, `short_description`, `long_description`, `category`,
`links` (homepage, coingecko, defillama, twitter), `contract_addresses` (chain + address pairs),
and a `checks` section recording liveness verification dates.

Logo subfolders follow the same structure as vaults: `original_logos/` and `formatted_logos/`.

**Source modules that read this data:**

| Module | Role |
|--------|------|
| [`eth_defi/stablecoin_metadata.py`](../stablecoin_metadata.py) | Reads YAML metadata and formatted logos; builds JSON index; uploads to R2 |
| [`scripts/erc-4626/export-protocol-metadata.py`](../../scripts/erc-4626/export-protocol-metadata.py) | Orchestrates stablecoin metadata + logo export to R2 |
| [`scripts/logos/post-process-logo.py`](../../scripts/logos/post-process-logo.py) | Converts original logos into formatted 256x256 PNG files |

### `curators.md`

Human-readable reference listing active DeFi vault curators and vault managers across
Morpho, Euler, IPOR Fusion, Lagoon Finance, Hyperliquid, GRVT, and Lighter.
Includes contact details (website, Twitter) and platform-specific sections.

This is a reference document — the machine-readable curator data lives in `feeds/curators/` YAML files.

## Data flow

```
                 ┌──────────────────────────────────────────────────────────────────┐
                 │  eth_defi/data/                                                  │
                 │                                                                  │
  YAML files ──► │  feeds/         ──► feed/sources.py ──► feed/scanner.py          │
                 │  feeds/curators ──► feed/sources.py ──► vault/curator.py ──► R2  │
                 │  vaults/        ──► vault/protocol_metadata.py ──────────── ► R2  │
                 │  stablecoins/   ──► stablecoin_metadata.py ─────────────── ► R2  │
                 │                                                                  │
  Logo files ──► │  */original_logos/ ──► post-process-logo.py                      │
                 │                           ▼                                      │
                 │                      */formatted_logos/ ──► *_metadata.py ──► R2  │
                 └──────────────────────────────────────────────────────────────────┘
```

The export pipeline (`scripts/erc-4626/export-protocol-metadata.py`) reads all three metadata
catalogues and uploads JSON indices plus logo assets to Cloudflare R2, where they are served to
the Trading Strategy frontend.

## Related READMEs

| Path | Description |
|------|-------------|
| [`feeds/README.md`](feeds/README.md) | Feed YAML schema and subfolder index |
| [`feeds/tracking.md`](feeds/tracking.md) | Progress tracker for feed YAML creation |
| [`vaults/README.md`](vaults/README.md) | Vault protocol YAML schema and logo conventions |
| [`../feed/README-feed.md`](../feed/README-feed.md) | Feed submodule architecture and configuration |
| [`../../scripts/erc-4626/README-vault-scripts.md`](../../scripts/erc-4626/README-vault-scripts.md) | ERC-4626 vault scripts overview |
