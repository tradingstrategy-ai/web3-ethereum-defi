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

The fix records a one-hour retry deadline beside the existing cache, under
the same file lock. Threads, processes and restarts therefore share the
cooldown. Failed and empty responses preserve the successful snapshot and
its modification time; cold caches remain empty rather than acquiring a
false successful snapshot. Success clears the deadline. A documented
`retry_cooldown` function argument permits deliberate adjustment without an
additional environment variable. Metadata lookup uses the expiring disk
cache instead of a permanent global dictionary.

Stale TVL remains fallback data, not a newly fetched API observation. This
change bounds outage traffic; it does not restore a healthy upstream feed.

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

- Focused offline suites: **56 passed, 2 skipped**. Coverage includes concurrent
  refresh failures with warm and cold caches, unchanged stale bytes and age,
  cooldown expiry and recovery, empty-response preservation, recovery after an
  initial empty metadata lookup, and all four YieldBasis admission conversions.
- Real Ethereum provider:
  `source .local-test.env && PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" timeout 180s poetry run pytest tests/yield_basis/test_yield_basis_integration.py -q`
  passed, **1 test**, on 3 October 2026. It exercises the Factory and valuation
  path and confirms the scanner's USD exchange rate.
- Manual real ForgeYields outage check used a temporary empty cache and wrapped
  the actual HTTP transport: two fetcher invocations produced **one HTTP
  request**, both returned unavailable data, and the retry deadline existed.
  The provider returned HTTP 500. Successful live-API coverage remains blocked
  by that outage; a fork test passing with cached data is not evidence of
  current upstream health.

The [script README](../../scripts/erc-4626/README-vault-scripts.md) documents the
cooldown, synthetic denominations and an opt-in live API check that uses a
temporary cache. Run it after upstream recovery, without repeated outage bursts.
