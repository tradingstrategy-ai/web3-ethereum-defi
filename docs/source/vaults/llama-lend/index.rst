Llama Lend API
--------------

`Llama Lend <https://lend.curve.fi/>`__ integration.

Llama Lend is Curve Finance's lending protocol powered by the liquidation protection mechanism
provided by LLAMMA (Lending Liquidating Automated Market Maker Algorithm).

LLAMMA is the market-making contract that rebalances the collateral of a loan. It is an algorithm
implemented into a smart contract which is responsible for liquidating and de-liquidating collateral
based on market conditions through arbitrage traders. Each individual market has its own AMM
containing the collateral and borrowable asset.

Based on the collateral provided, LLAMMA fixes specific price bands to liquidate portions of the
collateral rather than liquidating fully at a specific liquidation price, enabling soft liquidations.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/llama-lend>`__
- `Homepage <https://curve.fi/>`__
- `App <https://lend.curve.fi/>`__
- `Documentation <https://docs.curve.finance/crvUSD/amm/>`__
- `GitHub <https://github.com/curvefi>`__
- `Twitter <https://x.com/llamalend>`__
- `DefiLlama <https://defillama.com/protocol/curve-llamalend>`__

.. autosummary::
   :toctree: _autosummary_llama_lend
   :recursive:

   eth_defi.erc_4626.vault_protocol.llama_lend.vault
