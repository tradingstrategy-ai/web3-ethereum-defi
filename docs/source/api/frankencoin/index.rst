Frankencoin API
---------------

`Frankencoin <https://frankencoin.com/>`__ savings and equity vault integration.

Frankencoin is an over-collateralised, oracle-free Swiss franc stablecoin
protocol. Its svZCHF Savings Vaults wrap the Frankencoin savings module as
ERC-4626 vaults on Ethereum, Base and Gnosis.

The savings vaults have no protocol-wide management, performance, deposit, or
withdrawal fees. They do support an optional account-level referral fee that can
deduct up to 25% of earned interest for accounts that configure a referrer.

The :class:`~eth_defi.erc_4626.vault_protocol.frankencoin.shares.FrankencoinSharesVault`
adapter tracks the Ethereum FCS equity wrapper. It preserves backing TVL
separately from its bonding-curve reference valuation and reports conditional
redemption. See :doc:`../../vaults/frankencoin/index` for revenue sources,
strategy classifications and the targeted backfill procedure.

.. autosummary::
   :toctree: _autosummary_frankencoin
   :recursive:

   eth_defi.erc_4626.vault_protocol.frankencoin.vault
   eth_defi.erc_4626.vault_protocol.frankencoin.shares
   eth_defi.erc_4626.vault_protocol.frankencoin.constants
   eth_defi.erc_4626.vault_protocol.frankencoin.tags
   eth_defi.erc_4626.vault_protocol.frankencoin.backfill
