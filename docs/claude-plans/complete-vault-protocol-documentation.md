# Plan: Complete vault protocol documentation

**Save to:** `docs/claude-plans/complete-vault-protocol-documentation.md` before implementation

## Objective

Update all vault protocol documentation pages under `docs/source/vaults/` to match the Gearbox template structure with consistent Links sections.

## Template structure (from Gearbox)

Each documentation page should have:
1. **Title** - Protocol name with main link
2. **Description** - 1-2 paragraphs explaining the protocol
3. **Key features** (optional) - Bullet list of main features
4. **Fee structure** (optional) - If applicable
5. **Example vault contracts** (optional) - Links to deployed contracts
6. **Links section** - With `Links\n~~~~~` header containing:
   - Homepage
   - App
   - Documentation
   - GitHub
   - Twitter
   - DefiLlama (if available)
   - Audits (optional)
7. **API autosummary** - Module references

## Protocols requiring updates

### Group 1: Minimal documentation (need web research + full Links section)

| Protocol | File | Missing |
|----------|------|---------|
| ipor | `docs/source/vaults/ipor/index.rst` | Description, key features, Links section |
| euler | `docs/source/vaults/euler/index.rst` | Description, key features, Links section |
| lagoon | `docs/source/vaults/lagoon/index.rst` | Description, key features, Links section |

### Group 2: Has description, needs Links section header and links

| Protocol | File | Missing |
|----------|------|---------|
| plutus | `docs/source/vaults/plutus/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| silo | `docs/source/vaults/silo/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| yearn | `docs/source/vaults/yearn/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| harvest | `docs/source/vaults/harvest/index.rst` | Homepage, App, Documentation, GitHub (also fix wrong link text) |
| superform | `docs/source/vaults/superform/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| goat | `docs/source/vaults/goat/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| auto_finance | `docs/source/vaults/auto_finance/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| d2_finance | `docs/source/vaults/d2_finance/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| nashpoint | `docs/source/vaults/nashpoint/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| untangle | `docs/source/vaults/untangle/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| llamma | `docs/source/vaults/llamma/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| umami | `docs/source/vaults/umami/index.rst` | Homepage, App, Documentation, GitHub (fix Twitter format) |
| truefi | `docs/source/vaults/truefi/index.rst` | Homepage, App, Documentation, GitHub (has Twitter) |
| gains | `docs/source/vaults/gains/index.rst` | Add Links section with all links |

### Group 3: Needs minor additions

| Protocol | File | Missing |
|----------|------|---------|
| centrifuge | `docs/source/vaults/centrifuge/index.rst` | Homepage, App links |
| cap | `docs/source/vaults/cap/index.rst` | Documentation, GitHub links |
| upshift | `docs/source/vaults/upshift/index.rst` | GitHub link |
| summer | `docs/source/vaults/summer/index.rst` | Homepage, App, Documentation, GitHub |

### Group 4: Content issues to fix

| Protocol | File | Issue |
|----------|------|-------|
| usdai | `docs/source/vaults/usdai/index.rst` | Wrong content (copied from goat) - needs complete rewrite |

**Note:** Skip `deltr` as it's marked as "Unknown protocol" per user decision.

### Group 5: Already complete (no changes needed)

- gearbox, altura, maple, sky, ethena, term_finance, teller, yuzu_money, royco, usdd, liquidity_royalty, csigma, spectra, zerolend, eth_strategy, foxify, spark

## Implementation approach

For each protocol needing updates:

1. **Research** - Use web search to find:
   - Official homepage URL
   - App/dApp URL
   - Documentation URL
   - GitHub repository URL
   - Twitter handle (verify against homepage)

2. **Update documentation** - Edit the `index.rst` file to add:
   - Links section with `Links\n~~~~~` header
   - All available links in consistent format

3. **Format** - Ensure links follow the RST format:
   ```rst
   Links
   ~~~~~

   - `Homepage <https://protocol.com/>`__
   - `App <https://app.protocol.com/>`__
   - `Documentation <https://docs.protocol.com/>`__
   - `GitHub <https://github.com/protocol>`__
   - `Twitter <https://x.com/protocol>`__
   - `DefiLlama <https://defillama.com/protocol/protocol-name>`__
   ```

## Files to modify (22 files)

```
docs/source/vaults/usdai/index.rst          # Group 4 - content fix
docs/source/vaults/ipor/index.rst           # Group 1 - minimal
docs/source/vaults/euler/index.rst          # Group 1 - minimal
docs/source/vaults/lagoon/index.rst         # Group 1 - minimal
docs/source/vaults/plutus/index.rst         # Group 2 - needs Links
docs/source/vaults/silo/index.rst           # Group 2 - needs Links
docs/source/vaults/yearn/index.rst          # Group 2 - needs Links
docs/source/vaults/harvest/index.rst        # Group 2 - needs Links + fix
docs/source/vaults/superform/index.rst      # Group 2 - needs Links
docs/source/vaults/goat/index.rst           # Group 2 - needs Links
docs/source/vaults/auto_finance/index.rst   # Group 2 - needs Links
docs/source/vaults/d2_finance/index.rst     # Group 2 - needs Links
docs/source/vaults/nashpoint/index.rst      # Group 2 - needs Links
docs/source/vaults/untangle/index.rst       # Group 2 - needs Links
docs/source/vaults/llamma/index.rst         # Group 2 - needs Links
docs/source/vaults/umami/index.rst          # Group 2 - needs Links
docs/source/vaults/truefi/index.rst         # Group 2 - needs Links
docs/source/vaults/gains/index.rst          # Group 2 - needs Links
docs/source/vaults/centrifuge/index.rst     # Group 3 - minor
docs/source/vaults/cap/index.rst            # Group 3 - minor
docs/source/vaults/upshift/index.rst        # Group 3 - minor
docs/source/vaults/summer/index.rst         # Group 3 - minor
```

**Skipped:** `docs/source/vaults/deltr/index.rst` (Unknown protocol)

## Execution order

1. **Group 4** (usdai) - Fix wrong content first
2. **Group 1** (ipor, euler, lagoon) - Add full documentation
3. **Group 2** (14 protocols) - Add Links sections
4. **Group 3** (4 protocols) - Minor additions

For each protocol:
1. Use web search to find official Homepage, App, Documentation, GitHub, Twitter URLs
2. Check DefiLlama for protocol page (https://defillama.com/protocol/protocol-name)
3. Update the index.rst file with Links section
4. Verify link format matches template
