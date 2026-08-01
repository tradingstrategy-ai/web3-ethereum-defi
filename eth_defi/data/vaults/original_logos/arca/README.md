# Arca curator logo

## Sources

- Homepage: <https://www.arcalabs.com/>
- Official square favicon/lock-up: <https://www.arcalabs.com/hubfs/arca_square_576x576-1.png>
- Official horizontal lock-up: <https://www.arcalabs.com/hs-fs/hubfs/Arca%20Labs%20Horizontal%20Logo.png?width=2215&height=308&name=Arca%20Labs%20Horizontal%20Logo.png>
- Downloaded: 2026-07-30

## Files

| File | Description |
|------|-------------|
| `arca.wordmark.png` | Original square Arca lock-up retained for provenance; it includes the `arca` wordmark and is not used for vault artwork |
| `arca.source-horizontal.png` | Original horizontal Arca Labs lock-up used to obtain a higher-resolution mark |
| `arca.brandmark.png` | Transparent crop of the official orbit/A brandmark from the left side of the horizontal lock-up; no text |

The compact orbit/A brandmark is the appropriate small-screen representation of Arca. It is extracted from the official Arca Labs lock-up rather than sourced from a third-party logo catalogue.

## Processing

The standard `scripts/logos/post-process-logo.py` pipeline was run on
`arca.brandmark.png` to produce transparent 256×256 PNGs in
`eth_defi/data/vaults/formatted_logos/arca/`:

- `generic.png`
- `light.png`
- `dark.png`

