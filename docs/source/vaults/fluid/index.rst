Fluid API
---------

`Fluid <https://fluid.io/>`__ integration.

Fluid is a comprehensive DeFi liquidity layer by Instadapp that combines lending, borrowing,
and decentralised exchange capabilities. At its core is the Liquidity Layer, which consolidates
capital across built-on protocols, eliminating the need for individual protocols to independently
attract liquidity.

The Lending Protocol is a simple deposit-and-earn system where users supply assets to the Liquidity
Layer via ERC-4626 compliant fTokens. These tokens represent the user's stake and accrue interest
over time through the exchange price mechanism.

The protocol also includes the Vault Protocol for advanced borrowing with high LTV ratios and low
liquidation penalties, and a DEX Protocol built on top of the Liquidity Layer with Smart collateral
and Smart debt features.

Links
~~~~~

- `Homepage <https://fluid.io/>`__
- `Documentation <https://docs.fluid.instadapp.io/>`__
- `GitHub <https://github.com/Instadapp/fluid-contracts-public>`__
- `Twitter <https://x.com/0xfluid>`__
- `DefiLlama <https://defillama.com/protocol/fluid>`__
- `Audits <https://docs.fluid.instadapp.io/security/audits>`__
- `Example fToken (Plasma) <https://plasmascan.to/address/0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B>`__

Fees
~~~~

Fluid fTokens have fees internalised through the exchange price mechanism. Interest accrues to the
share price over time, and there are no explicit deposit or withdrawal fees.

.. autosummary::
   :toctree: _autosummary_fluid
   :recursive:

   eth_defi.erc_4626.vault_protocol.fluid.vault
