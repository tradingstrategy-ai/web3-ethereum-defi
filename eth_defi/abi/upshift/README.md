# Upshift ABI files

`TokenizedAccount.json` is the existing narrow ERC-4626-like interface used by
the Upshift vault integration.

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
on 2026-07-27. `requestRedeem` returns the claimable epoch and calendar date;
the request event itself contains only shares, holder and receiver, so the
manager persists the immediately-read `getWithdrawalEpoch()` state alongside
the validated event. `processAllClaimsByDate` is an operator settlement call,
not a GuardV0 manager call; GuardV0 only whitelists the deposit, instant
redeem, request and final claim surfaces.

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
