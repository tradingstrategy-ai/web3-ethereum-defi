LLAMMA API
-------------

LLAMMA (Lending Liquidating Automated Market Maker Algorithm) is the market-making contract that rebalances the collateral of a loan. It is an algorithm implemented into a smart contract which is responsible for liquidating and de-liquidating collateral based on market conditions through arbitrage traders. Each individual market has its own AMM containing the collateral and borrowable asset. E.g. the AMM of the ETH<>crvUSD contains of ETH and crvUSD.

- `Twitter <https://x.com/CurveFinance>`__

.. autosummary::
   :toctree: _autosummary_llamma
   :recursive:

   eth_defi.erc_4626.vault_protocol.llamma.vault