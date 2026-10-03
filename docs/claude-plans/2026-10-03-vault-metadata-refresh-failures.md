# ForgeYields refresh failures and denomination diagnostics

Investigation on 3 October 2026, following the RPC reduction deployment in
[#1614](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1614).
Changes are on `fix-vault-metadata-refresh-failures`; production state was
read only. No counters, cache files, metadata, reader state or prices were
reset or repaired directly in production.

## ForgeYields

The live [strategies endpoint](https://api.forgeyields.com/strategies) returns
HTTP 500 with `{"statusCode":500,"message":"Internal server error"}`.
The response does not identify the server-side cause. We cannot repair the
provider's backend or establish a replacement endpoint from this evidence.
This is a REST API outage, not an EVM RPC error.

The local amplification has a concrete cause in
[the strategy fetcher](../../eth_defi/erc_4626/vault_protocol/forgeyields/offchain_metadata.py):
`ForgeYieldsVault.fetch_tvl()` reads an hourly cache for each valuation. A
failed refresh falls back to a stale snapshot without changing its age, so
every subsequent valuation sees another expired cache and repeats the same
HTTP request. Twenty-four warnings appeared during the latest major-chain
scan. The separate process-global metadata dictionary also retained an empty
initial result indefinitely, preventing recovery in a looped process.

The final policy disables all offchain fetching with the line comment
`no longer working`. Strategy lookup reads the retained successful JSON copy
regardless of its age and never modifies its bytes or modification time.
Missing snapshots remain unavailable. History backfills fail explicitly before
changing prices because the normalised metadata copy has no raw history reports.
No cooldown files or network refreshes are needed while the source is disabled.

A headless-browser check at approximately 10:19 UTC confirmed that the app
renders “Failed to fetch strategies” after four HTTP-500 attempts to the same
endpoint. The public landing page also received HTTP 500 but displayed bundled,
hardcoded TVL/APY fallbacks, including `$1.81M` total TVL. The published app still
uses this endpoint; there is no evidence that our scanner uses an obsolete URL.
This does not establish the internal backend failure cause.

## Denomination metadata

The four frequently warned Ethereum addresses are the reviewed YieldBasis
yb-LP markets:

| Market | Address |
|---|---|
| WBTC | `0x651d4b8168488fa163d85304662e8278d4c55baa` |
| tBTC | `0x771f7290428d830ecd41e980745c327e507823ec` |
| WETH | `0x2b9c9f3bdceb5d8e36a4704f08a78fca53343cea` |
| cbBTC | `0x722fc3640ba007c3e9867ccdb0dca59f2e2f29f9` |

Production metadata already describes each as USD with
`_synthetic_usd_denomination=True`. Their denomination intentionally has no
ERC-20 address; contextual observations already incorporate the market
oracle. Generic admission and freshness created `VaultReaderState`, which
required an ERC-20 denomination and incorrectly diagnosed these correct
records as unavailable. The same mismatch occurs for Kinexys USD estimates.

The fix adds an explicit adapter declaration for synthetic USD valuations.
The common reader uses an exchange rate of one and records `token_symbol`
as USD for these adapters. Ordinary missing tokens remain unavailable.
Price-source classifications, oracle calculations and underlying exposure
are unchanged; the declaration does not turn BTC or ETH into stable assets.
See the [YieldBasis accounting documentation](../../eth_defi/yield_basis/README-YieldBasis.md).

Other missing denominations are not a single cache corruption incident.
The recent export-log tail contained generic ERC-4626, Mellow, Centrifuge
and Theo records with no configured scalar denomination. Representative
current-state `asset()` reads on Base's `DONOTUSE uSOL+` and Arbitrum's
`Bread ETH` reverted. Theo's contract returned USDC, but its adapter
deliberately withholds scalar valuation pending support for basket accounting.
A returned address alone does not prove that substituting that token is a
valid valuation method. These cases need protocol-specific review; no tokens
were guessed, no vaults were automatically blacklisted, and no history was
discarded.

## Checks

The initial investigation's offline suites passed 56 tests, and its real
Ethereum YieldBasis integration test passed. A temporary-cache real ForgeYields
check reproduced HTTP 500 and demonstrated the initial cooldown. These checks
preceded the final decision to disable fetching entirely.

All 12 focused ForgeYields tests passed on 3 October 2026. The tests verify that arbitrarily old metadata keeps its bytes and age,
a missing snapshot is not recreated, no HTTP request occurs, and offchain
history backfills abort explicitly. The ForgeYields fork test uses an isolated
metadata snapshot instead of the retired API, covering both unavailable and
retained NAV alongside its real contract checks.

The [script README](../../scripts/erc-4626/README-vault-scripts.md) documents the
retained metadata location and disabled history source. Production caches were
not modified by this work.
