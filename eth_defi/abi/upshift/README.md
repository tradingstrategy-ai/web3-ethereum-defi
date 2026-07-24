# Upshift ABI files

`TokenizedAccount.json` is the existing narrow ERC-4626-like interface used by
the Upshift vault integration.

`MultiAssetVault.json` is the narrow runtime interface used by the Upshift
multi-asset adapter. It was reduced from the shared verified implementation ABI
to the read-only methods needed by vault classification and historical pricing:

- `asset()`
- `assetsWhitelistAddress()`
- `lpTokenAddress()`
- `getSharePrice()`
- `getTotalAssets()`
- `depositsPaused()`
- `withdrawalsPaused()`
- `maxDepositAmount()`
- `maxWithdrawalAmount()`

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
