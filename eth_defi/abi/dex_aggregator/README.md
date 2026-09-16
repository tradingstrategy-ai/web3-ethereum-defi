# DEX aggregator router ABIs

Verified router ABIs used by `eth_defi.dex_aggregator.router_calldata` to
identify which aggregator routed a swap and to decode the user's slippage
bound (`minReturn` / `minAmountOut`) from the router calldata.

All ABIs were fetched on 2026-09-15 from Base (chain id 8453). None of the
routers below are proxies unless stated.

| File | Contract | Address on Base | Source |
|------|----------|-----------------|--------|
| `OKXDexRouter.json` | OKX DEX `DexRouter` (`v1.0.10-rfq-anti-arbitrage`) | `0x67d03631fe51b741c0c00c4e16eb662ac84381df` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x67d03631fe51b741c0c00c4e16eb662ac84381df) |
| `KyberSwapMetaAggregationRouterV2.json` | KyberSwap `MetaAggregationRouterV2` | `0x6131b5fae19ea4f9d964eac0408e4408b66337b5` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x6131b5fae19ea4f9d964eac0408e4408b66337b5) |
| `OneInchAggregationRouterV6.json` | 1inch `AggregationRouterV6` | `0x111111125421ca6dc452d289314280a0f8842a65` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x111111125421ca6dc452d289314280a0f8842a65) |
| `OneInchAggregationRouterV5.json` | 1inch `AggregationRouterV5` | `0x1111111254eeb25477b68fb85ed929f73a960582` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x1111111254eeb25477b68fb85ed929f73a960582) |
| `ParaswapAugustusV6_2.json` | Paraswap (Velora) `AugustusV6.2` | `0x6a000f20005980200259b80c5102003040001068` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x6a000f20005980200259b80c5102003040001068) |
| `ZeroExSettler.json` | 0x `Settler` (called through `AllowanceHolder` `0x0000000000001ff3684f28c67538d4d072c22734`) | `0x4f6f91599858bf0d19fabcf2c5d591fe13f7c059` | [Sourcify](https://sourcify.dev/server/v2/contract/8453/0x4f6f91599858bf0d19fabcf2c5d591fe13f7c059) |
| `Aggregator2f68.json` | Unnamed `Aggregator` (ERC-1967 proxy, implementation `0xffDe78F702a229E1c7A54E46CC600F33C95b4930`) | `0x2f68417a18da681589f4ea64b9cc9839209acff7` | [Basescan](https://basescan.org/address/0xffDe78F702a229E1c7A54E46CC600F33C95b4930#code) (Etherscan v2 `getsourcecode`) |

0x deploys a new `Settler` per release; add new addresses to
`AGGREGATOR_ROUTERS` in `router_calldata.py` when they appear on call paths.
The Settler ABI itself is stable across releases for `execute()`.

Routers that are only labelled, never decoded, and therefore have no ABI here:
CoW Protocol settlement, LI.FI diamond, Relay approval proxy, Binance Web3
Wallet router (its facets are unverified on both Sourcify and Basescan).
