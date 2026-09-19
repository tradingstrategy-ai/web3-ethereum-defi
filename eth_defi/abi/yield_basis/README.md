# YieldBasis interfaces

These are minimal, read-only interfaces extracted from the official
[`yield-basis/yb-core`](https://github.com/yield-basis/yb-core) source and its
Ethereum deployment record. The reviewed source commit is
`5082fa6c31c1ec3168a9d56f04131bf1716bd6a4`, the repository `master` revision
used on 2026-08-27. Update this note and re-run the fixed-block interface spike
when the upstream implementation changes.

- `Factory.json`: `Factory.vy` public stablecoin, market count and `markets()`
  tuple used by the runtime pre-scan.
- `LT.json`: `LT.vy`, including `pricePerShare()` and `updated_balances()`.
- `AMM.json`: `AMM.vy` identity and kill switch.
- `CurveCryptoPool.json`: Curve Cryptoswap `coins` and smoothed `price_oracle`
  views.

The Factory deployment is
`0x370a449FeBb9411c95bf897021377fe0B7D100c0`, and crvUSD is
`0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E`. The `MarketParameters` topic
is
`0x0a2f141c803b7bf5e04b3d7b621d659110855c736b446179264d6a61232d3f0b` for
`MarketParameters(uint256,address,address,address,address,address,address,address,address)`;
`idx`, `asset_token` and `cryptopool` are indexed, while `lt` is in the data
tuple. The implementation deliberately uses the public Curve pool
`price_oracle()` for the crvUSD conversion and does not substitute the
Factory's internal oracle.

The four reviewed LT addresses and fixed-point scales are maintained in
`eth_defi/yield_basis/addresses.py` and `eth_defi/yield_basis/metrics.py`. A
Factory or proxy implementation change must be re-reviewed before automatic
metadata updates are enabled.
