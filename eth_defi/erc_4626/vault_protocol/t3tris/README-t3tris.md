# T3tris vault protocol

T3tris is a tokenised vault protocol for professional asset managers. It builds
vaults around ERC-4626 shares and adds a dual-mode operational model:

- open vault mode, where users can use synchronous ERC-4626 style deposit and
  redemption flows;
- closed or asynchronous mode, where users submit deposit and redemption
  requests, wait for a settlement, and then claim shares or assets.

The public product is at [t3tris.finance](https://t3tris.finance/) and the live
vault application is at [earn.t3tris.finance/vaults](https://earn.t3tris.finance/vaults);
the older `app.t3tris.finance` links redirect there. The developer
documentation is at [docs.t3tris.finance](https://docs.t3tris.finance/). It
documents the T3tris TypeScript SDK (v0.8.2 in November 2026) rather than the
vault's Solidity ABI. The earlier `t3tris-finance/mdoc-t3tris` repository,
which the original research used, no longer exists.

The earlier T3tris documentation described the protocol architecture as:

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

`offchain_metadata.py` reads the vault page metadata shown in the T3tris app
from its REST API:

```text
GET https://earn.t3tris.finance/api/vaults/{chainId}/{vaultAddress}
GET https://earn.t3tris.finance/api/vaults
```

The detail response has a `vault` object; the list response has an `items`
array of vault objects. The adapter uses these vault fields:

| Field | Use |
| --- | --- |
| `name`, `displayName`, `symbol`, `displaySymbol` | Display name and symbol |
| `description` | Markdown vault description. Render it with the same precautions as any third-party content |
| `curatorName`, `curatorUrl` | Manager name and link |
| `verified`, `visibility` | T3tris curation and listing flags |
| `category`, `attributes`, `rating` | Short description and classification |
| `depositsDisabled` or `flags.depositEnabled` | App-level deposit flag. Execution uses the onchain `isDepositEnabled()` instead |
| `ipfsHash` | Onchain metadata pointer, usually empty |

Earlier endpoints are gone. `https://api.t3tris.finance/api/v1/...` has
returned 404 since October 2026, and the Ponder GraphQL indexer at
`https://api.t3tris.finance/graphql` returned 404 in November 2026. Neither is
used by the code. Onchain vault calls and events remain the source of truth
for execution and accounting.

Example vaults:

| Vault | Notes |
| --- | --- |
| `0x98e43a491a464f0886bc5e57207c340bbed0d01f` | Arbitrum First USDC. Permissionless; used by the deposit and redemption fork tests |
| `0x9984ad74c5fb6bec3888e14b4e453707d3be7f8f` | Arbitrum Gami USDC. Deposits disabled and both whitelists enabled at the test block |
| `0x394c4db21b6b429848e123272f206f1f9d8d74b0` | Arbitrum test vault with the deposit whitelist enabled |
| `0x5b93dd3eb7fd224565498045f5e1a2ebda49e672` | Robinhood Chain Morini vault |
| `0xd4d607239dcbdb5cc3a301266433810bb63c63bf` | Robinhood Chain Kingfisher vault |

The Arbitrum vaults share protocol address
`0x0000000000cc53b5fd649b80f08b05405779cc71`. Robinhood Chain (`chainId =
4663`) vaults must be classified through their T3tris `getGrossTVL`
interface rather than treated as generic ERC-4626 vaults.

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
| `IT3tris.json` | Not present as a frontend ABI export in the researched app bundle. Needed only for factory discovery/deployment features. |
| `ISafeOracle.json` | Not present as a frontend ABI export in the researched app bundle. The Anvil settlement driver uses a two-function inline fragment for `owner()` and `setTotalAssets(uint256)`, observed in onchain oracle transactions. |
| `ISilo.json` | Not present as a frontend ABI export in the researched app bundle. Needed only if we inspect silo internals. |

ABI source priority:

1. Live app bundle ABI, because it matches the frontend currently used against
   deployed vaults.
2. Observed onchain calls and events, decoded with that ABI.
3. Block explorer ABI if T3tris later verifies implementation contracts.

The current documentation at [docs.t3tris.finance](https://docs.t3tris.finance/)
describes SDK method fields, not Solidity signatures. For example, its
`requestDeposit` takes `amount`, `controller` and `permit2Data`, while the
deployed vault takes `requestDeposit(address receiver, bool unsafe, uint256
assets, bytes permit2Data)`. The earlier Markdown interface docs also differed
from the deployed ABI. Do not generate integration code from either without
checking the live app ABI and onchain transactions.

## Discovery and classification

The scanner discovers T3tris vaults from vault-local `DepositRequest`,
`RedeemRequest` and configuration events through Hypersync
(`get_t3tris_vault_discovery_events()` in `eth_defi/erc_4626/discovery_base.py`),
plus explicit leads in `constants.py` for deployments without such events.
`classification.py` identifies them by the `getGrossTVL()` getter. Onchain
values are used for TVL and accounting; the REST metadata only enriches the
vault row.

## Deposits and redemptions

`T3trisDepositManager` in `deposit_redeem.py` implements the closed-mode
lifecycle on top of the generic ERC-7540 manager. Open-mode synchronous
vaults, Permit2 deposits, `unsafe` flows and request decreases are not
implemented; the manager refuses them with typed preflight results.

| Flow step | T3tris call |
| --- | --- |
| Approve | ERC-20 `approve(vault, assets)`; the vault is the spender |
| Request deposit | `requestDeposit(owner, false, assets, "")` |
| Settle | Curator `settle(0, false, "")` after an oracle `setTotalAssets(uint256)` refresh |
| Claim deposit | `claimDeposit(owner)` |
| Request redeem | `requestRedeem(owner, owner, owner, false, shares)` |
| Claim redeem | `claimRedeem(owner, owner, false)` |

Verified on an Arbitrum fork against First USDC
`0x98e43a491a464f0886bc5e57207c340bbed0d01f`:

- Request ids are vault-wide epochs from `getCurrentDepositRequestId()` and
  `getCurrentRedeemRequestId()`. Per-owner state is keyed by
  `(owner, requestId)`: claimable when `getSettledDepositShares` or
  `getSettledRedeemAssets` is positive, pending when `getRequestDepositAmount`
  or `getRequestRedeemAmount` is positive.
- Claims are owner-wide and a new request first claims an earlier settled
  request in the same direction. The manager therefore allows one open
  request per owner and direction, and reads each ticket's own epoch.
- Each settlement needs an oracle valuation newer than the previous
  settlement, otherwise it reverts `Vault__StaleValuation()`. The T3tris
  curator documentation says the vault checks this order, not a maximum
  valuation age.
- Redemption settlement pulls the net shortfall from the curator wallet,
  which approves the vault beforehand.
- `convertToShares` and `convertToAssets` exclude entry and exit fees, so
  the inherited estimates are slightly high.

`force_settle()` refreshes the oracle as its `owner()` and settles as
`getCurator()` on Anvil. It rejects `mock` and `ignore_liquidity`.

### GuardV0 coverage

`whitelistT3tris(vault, notes)` registers only the four calls below, the vault
as approval destination and asset, and the denomination token.

| Manager call | Guard check | Positive proof | Negative proof |
| --- | --- | --- | --- |
| ERC-20 approval | Vault is the only approval destination | Executed through `performCall()` | Foreign spender rejected |
| `requestDeposit` | Receiver allowed; `unsafe` false; empty `permit2Data` | Request through `SimpleVaultV0` and a Safe module | Foreign receiver, `unsafe`, Permit2 payload rejected |
| `claimDeposit` | Receiver allowed | Shares claimed | Foreign receiver rejected |
| `requestRedeem` | Receiver, owner and `previousClaimReceiver` allowed; `unsafe` false | Request through both custody types | Each address position and `unsafe` rejected |
| `claimRedeem` | Owner and receiver allowed; `unsafe` false | Assets claimed | Each address position and `unsafe` rejected |
| Settlement, request decreases, forced claims, unsafe exits, `multicall` | Not registered | Not applicable | Rejected as not allowed |

Settlement proof comes from `force_settle()` on the fork. The tests are
`tests/guard/test_guard_simple_vault_t3tris.py` and
`tests/erc_4626/vault_protocol/test_t3tris_deposit_redeem.py`.

## Reference links

- T3tris homepage: https://t3tris.finance/
- T3tris vault app: https://earn.t3tris.finance/vaults
- Developer documentation: https://docs.t3tris.finance/
- Deposits and redemptions: https://docs.t3tris.finance/developers/deposits-and-redemptions
- Onchain reference: https://docs.t3tris.finance/developers/onchain-reference
- Settlement and epochs: https://docs.t3tris.finance/concepts/settlement-and-epochs
- Oracle and NAV: https://docs.t3tris.finance/concepts/oracle-and-nav
- Curator operations and fees: https://docs.t3tris.finance/curators/operations
- ERC-7540: https://eips.ethereum.org/EIPS/eip-7540
