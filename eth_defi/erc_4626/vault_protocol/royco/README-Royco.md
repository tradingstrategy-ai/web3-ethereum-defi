# Royco vault API

Royco is an Incentivised Action Market (IAM) protocol. A Royco market
incentivises an onchain action, such as depositing into another protocol's
ERC-4626 vault. The Royco API and smart contracts expose two related, but
different, concepts:

- Vault markets: Royco ERC-4626 wrapper contracts around an underlying vault.
- Recipe markets: generic onchain action markets that may not be ERC-4626 vaults.

For this integration, `RoycoVault` covers the Vault Market / WrappedVault
contracts only.

Relevant documentation:

- Royco API: https://docs.royco.org/royco-api/getting-started-with-the-royco-api
- Royco concepts: https://docs.royco.org/overview/key-concepts
- Recipes vs. Vault IAMs: https://docs.royco.org/for-incentive-providers/recipes-vs.-vaults-iams
- Creating a Vault IAM: https://docs.royco.org/for-incentive-providers/create-an-iam
- Market verification rules: https://docs.royco.org/for-incentive-providers/verify-a-market

## REST market API

Base URL:

```text
https://api.royco.org
```

Authentication:

```text
x-api-key: YOUR_API_KEY
```

Royco documentation also exposes the public demo key `ROYCO_DEMO`.

The main endpoint for discovering vault markets is:

```text
POST /api/v1/market/explore
```

Pagination is one-based. Use `page.index = 1` for the first page.

Example request for Vault Markets:

```json
{
  "filters": [
    {
      "id": "marketType",
      "value": 1,
      "condition": "eq"
    }
  ],
  "sorting": [
    {
      "id": "tvlUsd",
      "desc": true
    }
  ],
  "page": {
    "index": 1,
    "size": 100
  }
}
```

Important response fields for scanner integration:

| Field | Meaning |
| --- | --- |
| `id` | Royco global market id in `{chainId}_{marketType}_{marketId}` format. |
| `chainId` | EVM chain id. |
| `marketType` | `1` for Vault Markets, `0` for Recipe Markets. |
| `marketId` | Royco market contract address. For Vault Markets this is the wrapper address to scan as an ERC-4626 vault. |
| `underlyingVaultAddress` | The ERC-4626 vault wrapped by the Royco market. This is useful for diagnostics, but it is not the Royco wrapper address. |
| `name` | Royco display name. |
| `description` | Royco market description. |
| `category` | Market category, e.g. `default`. |
| `isActive` | Whether the Royco market is active. |
| `isVerified` | Whether Royco has verified the market metadata and flows. |
| `tvlUsd` | Royco API TVL in USD for this market. |
| `fillableUsd` | Available amount that can be filled. |
| `inputToken` | Input token metadata, including address, decimals, symbol and price. |

For ERC-4626 vault discovery, filter to:

- `marketType == 1`
- `isActive == true`, if only live markets are wanted
- `isVerified == true`, if only Royco-reviewed markets are wanted

Do not sum `marketType == 0` Recipe Market TVL as vault TVL. Recipe Markets
represent generic onchain actions and include categories such as Boyco that are
not comparable with ERC-4626 vault NAV.

## Onchain WrappedVault API

Royco Vault Markets use WrappedVault contracts. These wrappers implement the
usual ERC-4626 surface and add Royco-specific reward and preview methods.

Known addresses from the original integration:

| Contract | Address |
| --- | --- |
| WrappedVaultFactory | `0x75e502644284edf34421f9c355d75db79e343bca` |
| WrappedVault implementation | `0x3c44c20377e252567d283dc7746d1bea67eb3e66` |
| VaultMarketHub | `0xa97eCc6Bfda40baf2fdd096dD33e88bd8e769280` |

Useful ERC-4626 calls:

| Function | Use |
| --- | --- |
| `asset()` | Denomination token of the Royco wrapper. |
| `totalAssets()` | Current wrapper NAV in denomination token units. |
| `totalSupply()` | Royco wrapper share supply. |
| `convertToAssets(uint256)` | Converts wrapper shares to underlying asset units. |
| `maxDeposit(address)` | Deposit availability check. |
| `maxRedeem(address)` | Redeem availability check. |

Useful Royco-specific calls:

| Function | Use |
| --- | --- |
| `VAULT()` | Underlying vault wrapped by the Royco market. |
| `previewRateAfterDeposit(address,uint256)` | Royco-specific selector used by our classifier to detect WrappedVault contracts. |

The current classifier identifies Royco WrappedVaults by probing:

```text
previewRateAfterDeposit(address,uint256)
```

If the call succeeds, the vault gets `ERC4626Feature.royco_like` and is
instantiated as `RoycoVault`.

## TVL semantics

There are three different TVL figures that should not be mixed:

| Source | Meaning |
| --- | --- |
| ERC-4626 `totalAssets()` | Onchain NAV of a single Royco wrapper. This is what the vault scanner should use for individual vault rows. |
| Royco API `marketType=1` `tvlUsd` | Royco's offchain USD value for a single Vault Market. Good for discovery and sanity checks. |
| DefiLlama Royco TVL | Protocol-wide Royco TVL. This includes more than classic WrappedVault markets. |

DefiLlama's Royco adapter also tracks Royco Dawn / BoringVault style vaults:

- https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/main/projects/royco/index.js

Those contracts are not the same smart contract type as Royco WrappedVaults and
are not covered by `RoycoVault` unless separate support is added.

## DefiLlama TVL

DefiLlama's Royco protocol TVL is much larger than the sum of
`marketType=1` Vault Market rows returned by the Royco REST API.

On 2026-06-03:

- DefiLlama reported Royco protocol TVL around 31.7M USD.
- Royco REST API `marketType=1` Vault Markets summed to about 136k USD.
- Royco REST API `marketType=0` Recipe Markets contained much larger values,
  including Boyco-related rows. These are not ERC-4626 vault wrappers.

This difference is expected. DefiLlama's adapter counts protocol-wide token
balances, not only classic Royco WrappedVault contracts:

1. Recipe market balances from Goldsky subgraphs.
2. Vault market balances from Goldsky subgraphs.
3. Royco Dawn / BoringVault-style contracts hardcoded in the adapter.

The adapter source is:

- https://raw.githubusercontent.com/DefiLlama/DefiLlama-Adapters/main/projects/royco/index.js

The adapter configuration marks chains with `recipe`, `vault`, or both tags.
For most chains it reads:

- `royco-recipe-{chain}` subgraphs with `rawMarketTokenBalanceRecipes`.
- `royco-vault-{chain}` subgraphs with `rawMarketTokenBalanceVaults`.

Berachain is special-cased to:

```text
royco-ccdm-destination-boyco-berachain-mainnet
```

This Boyco / CCDM destination subgraph is where a large part of DefiLlama's
Berachain Royco TVL comes from. It is not represented as normal
`marketType=1` ERC-4626 Vault Markets in the Royco REST API.

DefiLlama also calls `sumBoringTvl()` for Royco Dawn / BoringVault-style
contracts. Known adapter entries include:

| Chain | Label | Vault |
| --- | --- | --- |
| Ethereum | RoyUSDCMainnet | `0x74D1fAfa4e0163b2f1035F1b052137F3f9baD5cC` |
| Sonic | RoyUSDCSonic | `0x74D1fAfa4e0163b2f1035F1b052137F3f9baD5cC` |
| Sonic | RoySonicUSDC | `0x45088fb2FfEBFDcf4dFf7b7201bfA4Cd2077c30E` |
| Plume | RoyPlumeUSDC | `0x83A6F6034ee44De6648B1885e24D837D8D98698f` |
| Plume | RoyUSDCPlume | `0x74D1fAfa4e0163b2f1035F1b052137F3f9baD5cC` |

These contracts are not the same smart contract type as `RoycoVault`
WrappedVaults. In the 2026-06-03 investigation, our generic ERC-4626
autodetection did not instantiate the Ethereum/Sonic Dawn contracts as Royco
WrappedVaults.

Takeaway:

- Use Royco REST API `marketType=1` for ERC-4626 wrapper discovery.
- Use onchain `totalAssets()` for per-vault rows in Trading Strategy.
- Use DefiLlama or a dedicated Goldsky/BoringVault integration for
  protocol-wide Royco TVL.
- Do not expect the Trading Strategy Royco vault page to match DefiLlama
  protocol TVL until Recipe IAM and Dawn / BoringVault support exists.

## Integration notes

As of the 2026-06-03 investigation:

- The Royco API returned 95 Vault Market rows.
- Only 31 were active and verified.
- Several missing Ethereum and Arbitrum wrapper addresses autodetected as
  `RoycoVault` when checked directly, so those are likely discovery/history
  gaps rather than classifier gaps.
- Base and Sonic wrappers expose `previewRateAfterDeposit(address,uint256)`,
  but the probe allowlist in `classification.py` did not include those chains.
- Corn Royco markets use chain id `21000000`, which is not currently in the
  scanner chain configuration.
- Royco Dawn / BoringVault contracts used by DefiLlama need a separate
  integration if Trading Strategy wants protocol-wide Royco coverage.

## Operational procedures

See [`scripts/erc-4626/README-Royco.md`](../../../../scripts/erc-4626/README-Royco.md)
for operator runbooks:

- Initial vault rescan after adding Royco support
- Fixing corrupted Royco tranche price data (purge + targeted rescan)

## Minimal discovery loop

Pseudocode for refreshing Royco Vault Market candidates:

```python
page_index = 1

while True:
    response = post(
        "https://api.royco.org/api/v1/market/explore",
        json={
            "filters": [
                {"id": "marketType", "value": 1, "condition": "eq"},
            ],
            "page": {"index": page_index, "size": 100},
        },
        headers={"x-api-key": api_key},
    )

    for market in response["data"]:
        if market["isActive"] and market["isVerified"]:
            chain_id = market["chainId"]
            wrapper_address = market["marketId"]
            underlying_vault = market["underlyingVaultAddress"]
            tvl_usd = market["tvlUsd"]

    if page_index >= response["page"]["total"]:
        break

    page_index += 1
```

The scanner should treat `marketId` as the Royco wrapper contract address. Use
`underlyingVaultAddress` only for diagnostics and cross-references.
