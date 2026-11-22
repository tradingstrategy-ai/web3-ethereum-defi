# GMX contract interfaces

The [contract loaders](../../gmx/contracts.py) bind the interfaces to the deployment on the selected chain. The local compiler artefacts also include bytecode for contracts used in fork tests.

| Filename | Source | Fetched at (UTC) | Reason to fetch |
| --- | --- | --- | --- |
| [Reader.json](Reader.json) | [Verified Arbitrum Reader at `0xfA26cBb46e2614609406de08CA1Dc7f70a684184`](https://sourcify.dev/server/v2/contract/42161/0xfA26cBb46e2614609406de08CA1Dc7f70a684184?fields=all) | Unrecorded | Read positions and market values. |
| [GlvReader.json](GlvReader.json) | Unrecorded | Unrecorded | Read GLV vault definitions and constituent markets. |
| [DataStore.json](DataStore.json) | Unrecorded | Unrecorded | Read GMX configuration and market state. |
| [ExchangeRouter.json](ExchangeRouter.json) | Unrecorded | Unrecorded | Submit router calls and orders. |
| [Oracle.json](Oracle.json) | Unrecorded | Unrecorded | Bind the GMX oracle interface. |
| [ReferralStorage.json](ReferralStorage.json) | Unrecorded | Unrecorded | Read referral codes. |
| [RoleStore.json](RoleStore.json) | Unrecorded | Unrecorded | Configure keeper roles on a test fork. |
| [OrderHandler.json](OrderHandler.json) | Unrecorded | Unrecorded | Execute pending orders on a test fork. |
| [balance.json](balance.json) | Unrecorded | Unrecorded | Read token balances and decimals. |
| [eventemitter.json](eventemitter.json) | Unrecorded | Unrecorded | Decode GMX events. |
| [GmxOrderExecutor.json](GmxOrderExecutor.json) | [Local Solidity source](../../../tests/gmx/forked-env-example/contracts/GmxOrderExecutor.sol) | Compiled locally | Deploy the fork test order executor. |
| [MockOracleProvider.json](MockOracleProvider.json) | [Local Solidity source](../../../tests/gmx/forked-env-example/contracts/mock/MockOracleProvider.sol), Solidity 0.8.20 | Compiled locally | Supply controlled prices in fork tests. |
