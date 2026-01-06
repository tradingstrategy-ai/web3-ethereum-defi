Gains and Ostium vault API
--------------------------

`Gains Network <https://gains.trade/>`__ gToken vault integration.

GToken-based vault smart contracts provide automated market making vaults for `perpetual futures
<https://tradingstrategy.ai/glossary/perpetual-future>`__ purely `onchain
<https://tradingstrategy.ai/glossary/onchain>`__. The trading engine uses gToken vaults for all
trading pairs, making it over 100x more capital efficient than traditional order book approaches.

Known implementations are:

- `gTrade <https://gains.trade/>`__, formerly known as Gains protocol and its gToken vaults
- `Ostium <https://ostium.app/vault>`__ vaults, also known as "Ostium LP" vaults where LP stands
  for `liquidity provider <https://tradingstrategy.ai/glossary/liquidity-provider>`__

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/gains-network>`__
- `Homepage <https://gains.trade/>`__
- `Vaults <https://gains.trade/vaults>`__
- `Documentation <https://docs.gains.trade/>`__
- `GitHub <https://github.com/GainsNetwork-org>`__
- `Twitter <https://x.com/GainsNetwork_io>`__
- `DefiLlama <https://defillama.com/protocol/gains-network>`__

This `Python-based <https://tradingstrategy.ai/glossary/python>`__ API allows you to automate
interactions with these vault in your application:

- Discover vaults and their features across multiple chains
- Historical data reading: share price, profit, TVL, deposits, redemptions
- Deposit into a vault from your application
- Redeem from the vault
- Query your vault positions
- Query vault redemption delays

For the example usage, see :py:class:`~eth_defi.erc_4626.vault_protocol.gains.vault.GainsVault` class.

.. autosummary::
   :toctree: _autosummary_gains
   :recursive:

   eth_defi.erc_4626.vault_protocol.gains.vault
   eth_defi.erc_4626.vault_protocol.gains.deposit_redeem
   eth_defi.erc_4626.vault_protocol.gains.testing

For more tutorials, like vault historical data, see `ERC-4626 <https://tradingstrategy.ai/glossary/erc-4626>`__ tutorials in :ref:`tutorials <tutorials>` section.
