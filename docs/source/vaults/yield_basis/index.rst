YieldBasis
==========

`YieldBasis <https://yieldbasis.com/>`__ provides leveraged liquidity-provider
shares (yb-LP/LT) for Curve Cryptoswap markets. The reviewed products in this
dataset are the Ethereum WBTC, cbBTC, tBTC and WETH unstaked markets.

The public record uses crvUSD as the denomination token. Its headline value
combines fundamental native-asset LT price-per-share with the market's
asset/crvUSD oracle, so BTC/ETH price volatility is intentionally visible in
the primary return. crvUSD can move away from one US dollar. The native-asset
price-per-share series is retained separately for comparison; neither CAGR is
a promise of yield.

Leverage, oracle smoothing, pool liquidity, temporary redemption discounts and
market shutdowns can make an executable exit differ from the fundamental
value. Generic ERC-4626 deposits, withdrawals and flow reconstruction are not
supported for these protocol-managed positions.

For the Python adapter and historical context storage, see the
:ref:`YieldBasis API <yield-basis>` documentation.
