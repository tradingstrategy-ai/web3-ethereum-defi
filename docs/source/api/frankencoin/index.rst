Frankencoin API
---------------

`Frankencoin <https://frankencoin.com/>`__ ERC-4626 savings vault integration.

Frankencoin is an over-collateralised, oracle-free Swiss franc stablecoin
protocol. Its svZCHF Savings Vaults wrap the Frankencoin savings module as
ERC-4626 vaults on Ethereum, Base and Gnosis.

The savings vaults have no protocol-wide management, performance, deposit, or
withdrawal fees. They do support an optional account-level referral fee that can
deduct up to 25% of earned interest for accounts that configure a referrer.

.. autosummary::
   :toctree: _autosummary_frankencoin
   :recursive:

   eth_defi.erc_4626.vault_protocol.frankencoin.vault
