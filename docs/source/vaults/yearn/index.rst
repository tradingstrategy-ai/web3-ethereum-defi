Yearn vaults API
----------------

`Yearn Finance <https://yearn.fi/>`__ vault integration.

Yearn Vaults (yVaults) are capital pools that automatically generate yield based on opportunities
present in the market. Vaults benefit users by socialising gas costs, automating the yield generation
and rebalancing process, and automatically shifting capital as opportunities arise. End users do not
need extensive knowledge of the underlying DeFi protocols and can use the vaults as passive-investing
strategies.

With yVaults v3, vaults can be made from a single strategy or a collection of multiple strategies
which balance funds between them. Users have more control over where they want their funds to go
and a wider range of risk appetites.

Links
~~~~~

- `Homepage <https://yearn.fi/>`__
- `App <https://yearn.fi/vaults>`__
- `Documentation <https://docs.yearn.fi/>`__
- `GitHub <https://github.com/yearn>`__
- `Twitter <https://x.com/yearnfi>`__
- `DefiLlama <https://defillama.com/protocol/yearn-finance>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.yearn.vault
   eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder
