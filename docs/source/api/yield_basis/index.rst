.. _yield-basis:

YieldBasis API
--------------

This module contains read-only support for the reviewed Ethereum
`YieldBasis <https://yieldbasis.com/>`__ Earn LT markets.

YieldBasis yb-LP shares are reported in crvUSD. The primary fundamental value
is the LT native-asset ``pricePerShare()`` multiplied by the market Curve
Cryptoswap ``price_oracle()``. Consequently, BTC/ETH price movement is part of
the headline stablecoin-denominated return. The contextual history also retains
native PPS, asset/crvUSD prices, effective and staked supply, and an optional
redemption diagnostic for dual-CAGR analysis.

The integration covers Ethereum markets 7 (WBTC), 8 (cbBTC), 9 (tBTC) and 10
(WETH). It is a read-only VaultBase adapter; generic ERC-4626 deposits,
withdrawals and flow reconstruction are not supported.

.. autosummary::
   :toctree: _autosummary_yield_basis
   :recursive:

   eth_defi.yield_basis.addresses
   eth_defi.yield_basis.contracts
   eth_defi.yield_basis.historical_context
   eth_defi.yield_basis.metrics
   eth_defi.yield_basis.tags
   eth_defi.yield_basis.vault
   eth_defi.yield_basis.vault_catalog
   eth_defi.yield_basis.vault_sync
