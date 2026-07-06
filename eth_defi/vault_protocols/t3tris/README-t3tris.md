# T3tris vault protocol

T3tris is a tokenised vault protocol for professional asset managers. It builds
vaults around ERC-4626 shares and adds a dual-mode operational model:

- open vault mode, where users can use synchronous ERC-4626 style deposit and
  redemption flows;
- closed or asynchronous mode, where users submit deposit and redemption
  requests, wait for a settlement, and then claim shares or assets.

The public product is at [t3tris.finance](https://t3tris.finance/) and the live
vault application is at [app.t3tris.finance/vaults](https://app.t3tris.finance/vaults).
The public developer documentation lives in the
[t3tris-finance/mdoc-t3tris](https://github.com/t3tris-finance/mdoc-t3tris)
repository.

T3tris documentation describes the protocol architecture as:

- `T3tris`, a factory and registry contract;
- `Vault`, an ERC-4626, ERC-20, AccessControl and UUPS proxy vault;
- `SafeOracle`, a NAV oracle;
- `SyncSilo`, `DepositSilo` and `RedeemSilo`, ERC-4626 silo contracts used for
  open, pending deposit and pending redemption liquidity.

The docs state that T3tris smart contract source code is not verified on-chain
and that only interfaces are published. Treat the public interface docs and the
live application ABI as the integration sources, not explorer-verified Solidity.

## ERC-7540 compatibility

[ERC-7540](https://eips.ethereum.org/EIPS/eip-7540) extends ERC-4626 with
asynchronous deposit and redemption requests. Its model has:

- `requestDeposit` and `requestRedeem` to create requests;
- pending, claimable and claimed lifecycle states;
- request identifiers;
- `DepositRequest` and `RedeemRequest` events;
- ERC-4626 claim functions, using overloaded `deposit`, `mint`, `withdraw` and
  `redeem` methods with a controller argument;
- optional async deposit and async redemption support, with ERC-165 interface
  detection.

T3tris matches the high-level ERC-7540 lifecycle:

- deposits and redemptions can be requested asynchronously;
- requests are settled in batches;
- users explicitly claim after settlement;
- request ids are emitted in events and exposed by getters;
- the vault keeps pending deposit, claimable deposit, pending redeem and
  claimable redeem accounting.

However, T3tris does not match the final ERC-7540 ABI exactly. It needs a
T3tris-specific adapter instead of using generic ERC-7540 method calls.

Current live app ABI signatures:

```solidity
function requestDeposit(
    address receiver,
    bool unsafe,
    uint256 assets,
    bytes calldata permit2Data
) external;

function requestRedeem(
    address receiver,
    address owner,
    address previousClaimReceiver,
    bool unsafe,
    uint256 shares
) external;

function claimDeposit(address receiver)
    external
    returns (uint256 claimedShares_);

function claimRedeem(address owner, address receiver, bool unsafe)
    external
    returns (uint256 withdrawnAssets_);
```

Main differences from ERC-7540:

| Area | ERC-7540 | T3tris live ABI |
| --- | --- | --- |
| Deposit request | `requestDeposit(uint256 assets, address controller, address owner) returns (uint256 requestId)` | `requestDeposit(address receiver, bool unsafe, uint256 assets, bytes permit2Data)` returns nothing |
| Redeem request | `requestRedeem(uint256 shares, address controller, address owner) returns (uint256 requestId)` | `requestRedeem(address receiver, address owner, address previousClaimReceiver, bool unsafe, uint256 shares)` returns nothing |
| Claim deposit | overloaded ERC-4626 `deposit(uint256 assets, address receiver, address controller)` or `mint(...)` | `claimDeposit(address receiver)` |
| Claim redeem | overloaded ERC-4626 `redeem(uint256 shares, address receiver, address controller)` or `withdraw(...)` | `claimRedeem(address owner, address receiver, bool unsafe)` |
| Request status getters | `pendingDepositRequest`, `claimableDepositRequest`, `pendingRedeemRequest`, `claimableRedeemRequest` | T3tris getters such as `getRequestDepositAmount`, `getSettledDepositShares`, `hasClaimableDeposit`, `previewClaimDeposit`, `getRequestRedeemAmount`, `getSettledRedeemAssets`, `hasClaimableRedeem`, `previewClaimRedeem` |
| Operator support | `isOperator`, `setOperator`, ERC-165 support | Not present in the live `IVaultAbi` found in the app bundle |
| Extra controls | Not part of ERC-7540 | `unsafe` booleans, Permit2 calldata, settlement/admin methods and silo accounting |

The request events are close to the ERC-7540 shape:

```solidity
event DepositRequest(
    address indexed receiver,
    address indexed owner,
    uint256 indexed requestId,
    address sender,
    uint256 assets
);

event RedeemRequest(
    address indexed receiver,
    address indexed owner,
    uint256 indexed requestId,
    address sender,
    uint256 shares
);
```

`receiver` appears to be the closest T3tris equivalent of the ERC-7540
controller for request accounting, but the naming differs from the standard.
Do not assume generic ERC-7540 code can substitute `controller` without checking
the exact flow being implemented.

For `eth_defi`, model T3tris as an ERC-4626-derived asynchronous vault protocol
with ERC-7540-like lifecycle semantics, but protocol-specific method selectors.

## Interface map

Useful ERC-4626 and ERC-20 methods present in the live app ABI:

```solidity
asset()
totalAssets()
totalSupply()
balanceOf(address)
allowance(address,address)
approve(address,uint256)
transfer(address,uint256)
transferFrom(address,address,uint256)
convertToAssets(uint256)
convertToShares(uint256)
deposit(uint256,address)
deposit(address,uint256,bytes)
mint(uint256,address)
mint(address,uint256,bytes)
withdraw(uint256,address,address)
redeem(uint256,address,address)
```

Useful T3tris read methods:

```solidity
getProtocol() returns (address protocol_)
getGrossTVL() returns (GrossTvlBreakdown memory breakdown_)
getEntryFee() returns (uint64 entryFeeWad_)
getExitFee() returns (uint64 exitFeeWad_)
getPerformanceFee() returns (uint64 performanceFeeWad_)
getManagementFee() returns (uint64 managementFeeWad_, uint32 managementFeeDays_)
getCurrentDepositRequestId() returns (uint256)
getLastDepositRequestId(address receiver) returns (uint256)
getRequestDepositAmount(address owner, uint256 requestId) returns (uint256)
getSettledDepositShares(address owner, uint256 requestId) returns (uint256)
hasClaimableDeposit(address owner) returns (bool)
previewClaimDeposit(address owner) returns (uint256)
getCurrentRedeemRequestId() returns (uint256)
getLastRedeemRequestId(address receiver) returns (uint256)
getRequestRedeemAmount(address owner, uint256 requestId) returns (uint256)
getSettledRedeemAssets(address owner, uint256 requestId) returns (uint256)
hasClaimableRedeem(address owner) returns (bool)
previewClaimRedeem(address owner) returns (uint256, uint256)
isVaultOpen() returns (bool)
isEndOfFund() returns (bool)
isDepositEnabled() returns (bool)
isTransferEnabled() returns (bool)
isDepositWhitelistEnabled() returns (bool)
isWithdrawWhitelistEnabled() returns (bool)
getSyncSilo() returns (address)
getDepositSilo() returns (address)
getRedeemSilo() returns (address)
getIpfsHash() returns (string)
getShareName() returns (string)
getShareSymbol() returns (string)
```

`getGrossTVL()` returns a tuple with:

| Field | Meaning |
| --- | --- |
| `totalManagedAssets` | assets currently managed by the vault |
| `pendingDeposits` | asset amount in pending deposit requests |
| `claimableRedeems` | asset amount claimable by redeem request owners |
| `grossTvl` | aggregate gross TVL figure used by T3tris |

## Offchain data

T3tris exposes a public GraphQL endpoint:

```text
https://api.t3tris.finance/graphql
```

The endpoint is a Ponder-style indexer and currently exposes these query roots:

```text
vault
vaults
asset
assets
position
positions
roleAssignment
roleAssignments
roleAdmin
roleAdmins
oracleOwner
oracleOwners
pendingAdminGrant
pendingAdminGrants
whitelistEntry
whitelistEntrys
vaultSnapshot
vaultSnapshots
activity
activitys
pendingRequest
pendingRequests
requestTx
requestTxs
_meta
```

Use GraphQL for discovery, metadata enrichment and sanity checks. Do not treat it
as authoritative for transaction execution or final accounting; onchain vault
calls and events remain the source of truth.

### Vault page metadata

The vault detail page uses a separate REST endpoint for the offchain metadata
shown in the application:

```text
GET https://api.t3tris.finance/api/v1/pages/vault/{chainId}/{vaultAddress}
```

For example:

```shell
curl -sS \
  "https://api.t3tris.finance/api/v1/pages/vault/42161/0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f" \
  | jq ".vault | {description, curatorName, curatorUrl, verified, depositsDisabled, showComposition, displayName, displaySymbol, category, attributes, rating, ipfsHash, visibility, visibilityLocked}"
```

The response is a page view model with these top-level keys:

```text
performance
positions
roles
snapshots
vault
```

`vault.description` is Markdown and should be rendered with the same precautions
as any third-party HTML-producing content. The example Arbitrum vault
`0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f` returns a non-empty Markdown
description, curator profile fields and a public visibility flag from this REST
endpoint, while `vault.ipfsHash` is an empty string. This means the current page
description for that vault is backend-curated REST metadata, not data read from
IPFS through the indexed `ipfsHash` field.

Important REST metadata fields:

| Field | Meaning |
| --- | --- |
| `description` | Markdown text shown on the vault page. |
| `curatorName` | Human-readable curator or manager name. |
| `curatorUrl` | Curator website URL shown by the app. |
| `verified` | T3tris UI verification flag. |
| `depositsDisabled` | UI-level deposit disable flag. Check onchain `isDepositEnabled()` or GraphQL `depositEnabled` before execution. |
| `showComposition` | Whether the UI should show vault composition data. |
| `displayName`, `displaySymbol` | Optional UI overrides for onchain vault name and symbol. |
| `onchainName`, `onchainSymbol` | Onchain name and symbol copied into the page model. |
| `category`, `attributes`, `rating` | Additional T3tris curation and risk classification fields. |
| `visibility`, `visibilityLocked` | Listing and visibility controls used by the app. |
| `apy` | App-level APY view. The detail endpoint currently returns `D`, `W`, `M`, `Y` and `ALL` buckets. |
| `depositorCount` | App-indexed depositor count. |
| `ppsEffectiveWad` | Effective price per share used by the app view. |

Adding an account query parameter can include account-specific page data:

```text
GET https://api.t3tris.finance/api/v1/pages/vault/{chainId}/{vaultAddress}?account={address}
```

The vault list endpoint also includes the same offchain metadata for discovery:

```text
GET https://api.t3tris.finance/api/v1/vaults
```

This endpoint returns an object with a `vaults` array. Its vault items include
`description`, `curatorName`, `curatorUrl`, `verified`, `depositsDisabled`,
`showComposition`, `displayName`, `displaySymbol`, `category`, `attributes`,
`rating`, `visibility` and `visibilityLocked`. The list APY object currently has
a different shape from the detail endpoint, using `m` and `allTime` fields.

Related page endpoints discovered from the app bundle:

| Endpoint | Use |
| --- | --- |
| `/api/v1/vaults/{chainId}/{vaultAddress}/performance?window={window}` | Performance chart data. |
| `/api/v1/vaults/{chainId}/{vaultAddress}/composition` | Vault composition data, used when `showComposition` is true. |
| `/api/v1/activity/{chainId}/{vaultAddress}?limit={limit}` | Vault activity feed. |
| `/api/v1/events/{chainId}/{vaultAddress}` | Curator/admin event timeline. This is separate from the Markdown description and was empty for the example vault during research. |
| `/api/v1/config` | App configuration, including chains, features, governance and pricing. |

GraphQL still exposes `ipfsHash` and the onchain `getIpfsHash()` getter exists
in the live ABI, so future vaults may use IPFS-backed metadata. For the current
application page, prefer the REST page endpoint when matching what users see in
the T3tris app.

Useful `vault` fields:

| Field group | Fields |
| --- | --- |
| Identity | `chainId`, `address`, `asset`, `name`, `symbol`, `shareName`, `shareSymbol`, `version` |
| Operators | `owner`, `curator`, `feeRecipient`, `protocol`, `oracle`, `isSafeOracle` |
| Silos | `syncSilo`, `depositSilo`, `redeemSilo` |
| Fees | `entryFeeWad`, `exitFeeWad`, `performanceFeeWad`, `managementFeeWad`, `managementFeeDays`, `unclaimedSharesFee`, `pendingFeeShares` |
| Status | `transferEnabled`, `depositWhitelistEnabled`, `withdrawWhitelistEnabled`, `depositEnabled`, `paused`, `vaultOpen`, `endOfFund` |
| Accounting | `totalAssets`, `totalSupply`, `totalNetAssets`, `totalNetSupply`, `pricePerShareWad`, `hwmWad`, `lastSettlementTs` |
| Gross TVL | `grossTvl`, `grossManagedAssets`, `grossPendingDeposits`, `grossClaimableRedeems` |
| Polling | `pollLastTotalAssets`, `lastValuationTs`, `lastPollOkTs`, `pollFailures` |
| Deployment | `referralCode`, `salt`, `deployCalldata`, `createdAtBlock`, `createdAtTs`, `updatedAtBlock`, `updatedAtTs` |

Useful `asset` fields:

```text
id
chainId
address
symbol
name
decimals
canonicalId
logoUri
source
priceUsd
priceUpdatedAt
```

Useful `position` fields:

```text
chainId
vault
account
pendingDepositAssets
claimableDepositShares
ownedShares
pendingRedeemShares
claimableRedeemAssets
recoverableRedeemSiloShares
depositedAssets
withdrawnAssets
costBasisAssets
realizedPnlAssets
firstDepositTs
lastUpdatedBlock
lastUpdatedTs
```

Useful request and activity tables:

| Type | Useful fields |
| --- | --- |
| `pendingRequest` | `chainId`, `vault`, `side`, `requestId`, `account`, `amount`, `updatedAtBlock`, `updatedAtTs` |
| `requestTx` | `chainId`, `vault`, `side`, `requestId`, `account`, `txHash`, `logIndex`, `amount`, `timestamp` |
| `vaultSnapshot` | `chainId`, `vault`, `tier`, `ts`, `blockNumber`, `pricePerShareWad`, `ppsEffectiveWad`, `totalAssets`, `totalSupply` |
| `activity` | `chainId`, `txHash`, `logIndex`, `vault`, `kind`, `category`, `account`, `amount`, `extra`, `blockNumber`, `timestamp` |

Example discovery query:

```graphql
{
  vaults(limit: 100) {
    items {
      chainId
      address
      asset
      name
      symbol
      shareName
      shareSymbol
      owner
      curator
      protocol
      oracle
      version
      vaultOpen
      totalAssets
      totalSupply
      totalNetAssets
      totalNetSupply
      pricePerShareWad
      grossTvl
      grossManagedAssets
      grossPendingDeposits
      grossClaimableRedeems
      createdAtBlock
      createdAtTs
      updatedAtBlock
      updatedAtTs
    }
  }
}
```

As of the initial research, the app-indexed live vaults were on Arbitrum
(`chainId = 42161`) and shared protocol address
`0x0000000000cc53b5fd649b80f08b05405779cc71`.

Example vaults seen in the API:

| Vault | Notes |
| --- | --- |
| `0x394c4db21b6b429848e123272f206f1f9d8d74b0` | Arbitrum USDC test vault with non-zero accounting and pending deposits during research |
| `0x98e43a491a464f0886bc5e57207c340bbed0d01f` | Arbitrum USDC vault observed in earlier app data |

## ABI files and sources

No verified Solidity source or canonical JSON ABI was found on public block
explorers during the initial research. Sourcify and Routescan did not expose a
usable verified implementation ABI for the sampled vault and protocol contracts.
The public T3tris GitHub repositories also did not contain Solidity source or
JSON ABI artefacts for the deployed vault protocol.

Local ABI files:

| File | Source | Notes |
| --- | --- | --- |
| `eth_defi/abi/t3tris/IVault.json` | `IVaultAbi` exported by the live app chunk `https://app.t3tris.finance/_next/static/chunks/13etv-4ylf72t.js` | Best current source found for deployed vault ABI. The chunk filename is content-hashed and may change, so re-check the current app bundle before committing an ABI update. |
| `eth_defi/abi/t3tris/Multicall.json` | Separate `multicall(bytes[])` fragment used by the same app chunk against vault addresses | The frontend keeps this as a small local ABI fragment instead of including it in `IVaultAbi`. |

Other T3tris contract ABIs were not saved yet:

| ABI | Reason |
| --- | --- |
| `IT3tris.json` | Not present as a frontend ABI export in the researched app bundle. Needed only for factory discovery/deployment features. For scanner support, vault-level GraphQL discovery may be enough. |
| `ISafeOracle.json` | Not present as a frontend ABI export in the researched app bundle. Needed only if we read oracle state directly. |
| `ISilo.json` | Not present as a frontend ABI export in the researched app bundle. Needed only if we inspect silo internals. |

ABI source priority:

1. Live app bundle ABI, because it matches the frontend currently used against
   deployed vaults.
2. Published interface docs in
   [mdoc-t3tris](https://github.com/t3tris-finance/mdoc-t3tris), because T3tris
   explicitly publishes interfaces rather than verified Solidity.
3. Block explorer ABI if T3tris later verifies implementation contracts.

Known doc drift:

- The Markdown docs list `requestDeposit(uint256 assets, address receiver, bytes
  permit2Data)`, but the live app ABI uses `requestDeposit(address receiver,
  bool unsafe, uint256 assets, bytes permit2Data)`.
- The Markdown docs list `requestRedeem(uint256 shares, address receiver,
  address owner, address previousClaimReceiver)`, but the live app ABI uses
  `requestRedeem(address receiver, address owner, address previousClaimReceiver,
  bool unsafe, uint256 shares)`.
- The Markdown docs list `getPerfFee()`, but the live app ABI exposes
  `getPerformanceFee()`.
- Some getter argument order in the Markdown docs is stale. The live app ABI has
  `getRequestDepositAmount(address owner, uint256 requestId)` and
  `getRequestRedeemAmount(address owner, uint256 requestId)`.

Because of this drift, do not generate integration code directly from the
Markdown signatures without comparing them to the current live app ABI.

## Integration notes for eth_defi

Initial scanner support can start with:

1. Discover vaults from GraphQL `vaults`.
2. Confirm each vault onchain with `asset()`, `totalAssets()`, `totalSupply()`,
   `getProtocol()` and, if needed, `version()`.
3. Classify T3tris vaults by `getProtocol()` returning the known T3tris protocol
   address or by the presence of uniquely T3tris getters such as
   `getGrossTVL()` and `getCurrentDepositRequestId()`.
4. Use onchain values for final TVL/accounting and GraphQL values as metadata
   or sanity checks.

Trading support needs a protocol-specific async deposit and redeem manager:

| Flow step | T3tris call |
| --- | --- |
| Request deposit | `requestDeposit(receiver, unsafe, assets, permit2Data)` |
| Check pending deposit | `getRequestDepositAmount(owner, requestId)` |
| Check claimable deposit | `hasClaimableDeposit(owner)` or `previewClaimDeposit(owner)` |
| Claim deposit | `claimDeposit(receiver)` |
| Request redeem | `requestRedeem(receiver, owner, previousClaimReceiver, unsafe, shares)` |
| Check pending redeem | `getRequestRedeemAmount(owner, requestId)` |
| Check claimable redeem | `hasClaimableRedeem(owner)` or `previewClaimRedeem(owner)` |
| Claim redeem | `claimRedeem(owner, receiver, unsafe)` |

Guard and transaction whitelisting must use the T3tris selectors above. Existing
ERC-7540 whitelists for standard `requestDeposit(uint256,address,address)` or
`requestRedeem(uint256,address,address)` will not cover T3tris.

## Reference links

- T3tris homepage: https://t3tris.finance/
- T3tris vault app: https://app.t3tris.finance/vaults
- T3tris GraphQL endpoint: https://api.t3tris.finance/graphql
- T3tris docs repository: https://github.com/t3tris-finance/mdoc-t3tris
- Introduction: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/01-introduction.md
- Architecture: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/01-architecture.md
- Vault interface: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/02-vault-interface.md
- Vault getters: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/03-vault-getters.md
- Protocol factory: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/05-protocol-factory.md
- Events reference: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/08-events-reference.md
- Integration guide: https://raw.githubusercontent.com/t3tris-finance/mdoc-t3tris/main/docs/en/04-developers/10-integration-guide.md
- ERC-7540: https://eips.ethereum.org/EIPS/eip-7540
