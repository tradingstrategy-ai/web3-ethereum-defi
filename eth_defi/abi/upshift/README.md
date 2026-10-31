# Upshift ABI files

`TokenizedAccount.json` includes the verified legacy deposit, dated request,
claim and native events. It was refreshed on 2026-10-30 against Core USDC proxy
`0xE9B725010A9E419412ed67d0fA5f3A5f40159D32` and implementation
[`0xf2cd14f02b4fdc0d26681fbc7f60a11b8378f96d`](https://etherscan.io/address/0xf2cd14f02b4fdc0d26681fbc7f60a11b8378f96d#code),
using Etherscan's verified implementation ABI. Its three-argument
`requestRedeem` pays immediately at zero lag; standard `redeem` reverts.

`MultiAssetVault.json` is the narrow runtime interface used by the Upshift
multi-asset adapter. It was reduced from the shared verified implementation ABI
to the methods needed by vault classification, historical pricing, deposits and
redemptions:

- `deposit(address,uint256,address)`
- `asset()`
- `assetsWhitelistAddress()`
- `lpTokenAddress()`
- `getSharePrice()`
- `previewDeposit(address,uint256)`
- `getTotalAssets()`
- `depositCap()`
- `depositsPaused()`
- `withdrawalsPaused()`
- `maxDepositAmount()`
- `maxWithdrawalAmount()`
- `globalLiabilityShares()`
- `previewRedemption(uint256,bool)`
- `instantRedeem(uint256,address)`
- `requestRedeem(uint256,address)`
- `getWithdrawalEpoch()`
- `processAllClaimsByDate(uint256,uint256,uint256,uint256)`
- `getBurnableAmountByReceiver(uint256,uint256,uint256,address)`
- `claim(uint256,uint256,uint256,address)`

The redemption functions and standard `Withdraw` event were verified against
the shared implementation at
[`0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2`](https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code)
on 2026-07-27, with the complete verified interface refreshed on 2026-10-30.
`getWithdrawalEpoch()` returns **year, month, day, epoch**. The multi-asset
request event omits the date, so the manager reads it at the request receipt's
block. The legacy event includes date and fixed net entitlement inputs.
`processAllClaimsByDate` is public after maturity and directly pays and clears
claims; it does not unlock a later user claim. GuardV0 grants the asset manager
only deposit, request, claim and the supported multi-asset instant surface.

`ISendersAllocationWhitelist.json` exports the exact two runtime methods from
`ISendersWhitelist` and `ISendersAllocationWhitelist` in that same verified
multi-asset source. Quota validation uses `checkAndRecordDeposit` through a
read-only `eth_call` from the configured vault; it does not persist a quota
change or require a funding transaction.

The protocol's claimability getter is keyed by the scheduled date and receiver,
not an individual request id. Multiple requests for the same receiver and date
are consequently processed and claimed as one receiver/date aggregate; the
manager ticket preserves that aggregate identity and must not present it as an
individually segregated claim.

`EnableOnlyAssetsWhitelist.json` is the narrow runtime interface used to
resolve the ordered denomination-token whitelist of an Upshift multi-asset
vault. It contains only `getWhitelistedAssets()` and was reduced from the
shared verified implementation ABI below.

`IMultiAssetVaultEvents.json` is an event-only interface for Upshift
`multiAssetVault` discovery. It contains the custom multi-asset deposit event
and the matching withdrawal request/processed events:

```solidity
event Deposit(address assetIn, uint256 amountIn, uint256 shares, address indexed senderAddr, address indexed receiverAddr);
event WithdrawalRequested(uint256 shares, address indexed holderAddr, address indexed receiverAddr);
event WithdrawalProcessed(uint256 assetsAmount, address indexed receiverAddr);
```

The deposit event topic is
`0xc436f473cd90c9b4dd731856a14b80f713d384a1688a506d4230140c5b36d5cd`.
This topic has been observed in Tori and Earn ctUSD proxy logs. The withdrawal
event topics are included from the fetched shared implementation ABI so vaults
that later emit Upshift-native withdrawal events get accurate redemption
counters.

Relevant sources:

- Upshift API docs: <https://docs.upshift.finance/developer-docs/api-reference>
- Tori Ecosystem Vault: <https://api.upshift.finance/v1/tokenized_vaults/0xcd69123b3FBBfC666E1f6a501da27B564C00De54>
- Earn ctUSD: <https://api.upshift.finance/v1/tokenized_vaults/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce>
- Tori proxy ABI on Sourcify: <https://repo.sourcify.dev/contracts/partial_match/1/0xcd69123b3FBBfC666E1f6a501da27B564C00De54/metadata.json>
- Earn ctUSD proxy ABI on Sourcify: <https://repo.sourcify.dev/contracts/partial_match/1/0xc87DBBB8C67e4F19fCD2E297c05937567b2572Ce/metadata.json>
- Shared implementation on Etherscan: <https://etherscan.io/address/0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2#code>
- Shared implementation ABI via Routescan API: <https://api.routescan.io/v2/network/mainnet/evm/1/etherscan/api?module=contract&action=getabi&address=0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2>

The public explorer/Sourcify ABI for Tori and Earn ctUSD is the
`TransparentUpgradeableProxy` ABI, so it exposes only proxy administration
events. Routescan reports both proxies use the shared implementation
`0xEB5f80aCEa6060764E91c185bE93752Ab40F01c2`. Discovery needs an
implementation-level event interface because logs are emitted at the proxy
address but decoded with the implementation event ABI.


Wildcat variant
---------------

Wildcat proxy `0x18EE038C114a07f4B08b420fb1E4149a4F357249` used verified
implementation [`0xaacc687cea8cd706bd7a56be0f7ad85d420767dc`](https://etherscan.io/address/0xaacc687cea8cd706bd7a56be0f7ad85d420767dc#code)
on 2026-10-30. Its deposit function enforces `maxDepositAmount` but has no
`depositCap` getter or global-cap check. The adapter recognises that exact
ERC-1967 implementation when the optional getter reverts; it never treats an
unknown implementation's unreadable cap as unlimited. Its older sender list
has no receiver-allocation call in the deposit path. Shared deposit/request/
claim signatures remain in the runtime multi-asset ABI.
