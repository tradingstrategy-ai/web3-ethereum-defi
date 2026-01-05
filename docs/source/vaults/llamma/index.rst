LLAMMA API
-------------

`Curve Finance <https://curve.fi/>`__ LLAMMA integration.

LLAMMA (Lending Liquidating Automated Market Maker Algorithm) is the market-making contract that
rebalances the collateral of a loan. It is an algorithm implemented into a smart contract which
is responsible for liquidating and de-liquidating collateral based on market conditions through
arbitrage traders. Each individual market has its own AMM containing the collateral and borrowable
asset.

LLAMMA forms part of Curve's lending infrastructure, including crvUSD and LlamaLend. Based on the
collateral provided, LLAMMA fixes specific price bands to liquidate portions of the collateral
rather than liquidating fully at a specific liquidation price, enabling soft liquidations.

Links
~~~~~

- `Homepage <https://curve.fi/>`__
- `App <https://lend.curve.fi/>`__
- `Documentation <https://docs.curve.finance/crvUSD/amm/>`__
- `GitHub <https://github.com/curvefi>`__
- `Twitter <https://x.com/CurveFinance>`__
- `DefiLlama <https://defillama.com/protocol/curve-llamalend>`__

.. autosummary::
   :toctree: _autosummary_llamma
   :recursive:

   eth_defi.erc_4626.vault_protocol.llamma.vault