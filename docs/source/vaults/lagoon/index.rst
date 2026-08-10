Lagoon Finance API
------------------

`Lagoon Finance <https://lagoon.finance/>`__ integration.

Lagoon provides open, general-purpose, secure vault infrastructure to build and scale onchain
yield products. The platform is designed for asset managers, DAOs, DeFi protocols and market
makers who need flexible vault infrastructure.

Powered by the ERC-7540 standard (asynchronous vaults), curators manage deposits and withdrawals
asynchronously, while users can join any public vault to start earning on their assets. The
infrastructure is built on top of Safe, leveraging Zodiac modules for security.

Key features:

- ERC-7540 asynchronous vault standard for managed deposits and withdrawals
- Built on Safe with Zodiac modules for institutional-grade security
- Smart contract code reviewed seven times by reputable firms
- Vault access controls with optional KYC/KYB integration
- CoW Protocol integration for trade execution

Withdrawal timing metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~

The scanner reads each vault's ``averageSettlement`` field from Lagoon's app
metadata and exports it as ``estimated_settlement`` (in seconds) for
backtesting. It measures an observed or curator-provided settlement cadence,
not the time at which a redemption becomes claimable. Curators can settle
earlier, later, or not at all; Lagoon's ERC-7540 contracts do not make this a
binding deadline. Accordingly, the scanner exports ``withdrawal_delay_type``
as ``delay`` but leaves ``min_withdrawal_period`` and
``max_withdrawal_period`` as ``null``. See :doc:`../withdrawal-period-audit`
for the public export contract.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/lagoon-finance>`__
- `Homepage <https://lagoon.finance/>`__
- `App <https://app.lagoon.finance/>`__
- `Documentation <https://docs.lagoon.finance/>`__
- `GitHub <https://github.com/hopperlabsxyz>`__
- `Twitter <https://x.com/lagoon_finance>`__
- `DefiLlama <https://defillama.com/protocol/lagoon>`__

.. autosummary::
   :toctree: _autosummary_lagoon
   :recursive:

   eth_defi.erc_4626.vault_protocol.lagoon.vault
   eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem
   eth_defi.erc_4626.vault_protocol.lagoon.config
   eth_defi.erc_4626.vault_protocol.lagoon.deployment
   eth_defi.erc_4626.vault_protocol.lagoon.analysis
   eth_defi.erc_4626.vault_protocol.lagoon.beacon_proxy
   eth_defi.erc_4626.vault_protocol.lagoon.cowswap
   eth_defi.erc_4626.vault_protocol.lagoon.lagoon_compatibility
   eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata
   eth_defi.erc_4626.vault_protocol.lagoon.testing
   eth_defi.erc_4626.vault_protocol.lagoon.velora
