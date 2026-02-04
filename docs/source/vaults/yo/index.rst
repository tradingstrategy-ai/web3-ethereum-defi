Yo API
------

`Yo <https://www.yo.xyz/>`__ integration.

Yo is a decentralised yield optimisation platform that automatically allocates user assets
across multiple DeFi protocols and blockchains to maximise risk-adjusted returns. It uses
algorithmic balancing powered by Exponential.fi's risk ratings to optimise yield while
managing exposure, and continuously rebalances to maintain optimal positioning.

The YoVault_V2 is an ERC-4626 compliant vault with an asynchronous redemption mechanism.
Users can deposit assets and request redemptions which are fulfilled by operators, enabling
cross-chain asset management.

Key features:

- Multi-chain yield optimisation across 40+ DeFi protocols
- Risk-adjusted optimisation powered by Exponential.fi
- Continuous rebalancing to capture yield opportunities
- Configurable deposit and withdrawal fees
- Asynchronous redemption for cross-chain operations

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/yo>`__
- `Homepage <https://www.yo.xyz/>`__
- `Documentation <https://docs.yo.xyz/>`__
- `GitHub <https://github.com/yoprotocol/core>`__
- `Twitter <https://x.com/yield>`__
- `DefiLlama <https://defillama.com/protocol/yo>`__
- `Contract on Etherscan <https://etherscan.io/address/0x0000000f2eb9f69274678c76222b35eec7588a65>`__


.. autosummary::
   :toctree: _autosummary_yo
   :recursive:

   eth_defi.erc_4626.vault_protocol.yo.vault
