.. _yield-basis:

YieldBasis API
--------------

This module contains read-only support for the reviewed Ethereum
`YieldBasis <https://yieldbasis.com/>`__ Earn LT markets.

Official resources
~~~~~~~~~~~~~~~~~~

- `Documentation <https://docs.yieldbasis.com/>`__
- `Smart-contract source <https://github.com/yield-basis/yb-core>`__
- `YieldBasis on X <https://x.com/yieldbasis>`__
- `Audits and bug bounties <https://docs.yieldbasis.com/user/reference/audits>`__
- `Fee documentation <https://docs.yieldbasis.com/user/protocol/fee-mechanics>`__
- `YieldBasis on DefiLlama <https://defillama.com/protocol/yield-basis>`__

YieldBasis yb-LP shares use a synthetic USD accounting denomination. The
primary value is the underlying returned by ``preview_withdraw()`` multiplied
by the market Curve Cryptoswap ``price_oracle()``. Consequently, both the
Temporary Redemption Discount and BTC/ETH price movement are part of the
headline USD return. The contextual history also retains fundamental
underlying PPS, asset/crvUSD source prices, redemption inputs, and effective
and staked supply for dual-CAGR analysis. A fixed 10-basis-point generic
USD-stablecoin conversion estimate is exposed separately as both the entry and
exit cost; it is not part of the historical equity curve. The YieldBasis
examiner models a new depositor at fundamental PPS and an exit at redemption
value, because TRD affects the latter but not the former.

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
