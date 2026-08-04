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

The deposit manager detects a global shutdown or full vault-wide deposit limit
before creating a transaction. When the selected account has already approved
the requested assets, it also simulates that exact deposit and returns a typed
admission rejection for a confirmed EVM revert. It does not treat a missing
approval or an RPC failure as a vault closure.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/yearn>`__
- `Homepage <https://yearn.fi/>`__
- `App <https://yearn.fi/vaults>`__
- `Documentation <https://docs.yearn.fi/>`__
- `GitHub <https://github.com/yearn>`__
- `Twitter <https://x.com/yearnfi>`__
- `DefiLlama <https://defillama.com/protocol/yearn-finance>`__

.. autosummary::
   :toctree: _autosummary_yearn
   :recursive:

   eth_defi.erc_4626.vault_protocol.yearn.deposit_redeem
   eth_defi.erc_4626.vault_protocol.yearn.vault
   eth_defi.erc_4626.vault_protocol.yearn.compounder
   eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder
