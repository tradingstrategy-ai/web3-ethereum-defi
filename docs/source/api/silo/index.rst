Silo Finance API
----------------

The Silo Protocol is a non-custodial lending primitive that creates programmable risk-isolated markets known as silos. Any user with a wallet can lend or borrow in a silo in a non-custodial manner. Silo markets use the peer-to-pool, overcollateralized model, where the value of a borrower's collateral always exceeds the value of their loan.

Silo is the main component of the protocol. It implements lending logic, manages and isolates risk, acts as a vault for assets, and performs liquidations. Each Silo is composed of the unique asset for which it was created (ie. UNI) and bridge assets (ie. ETH and SiloDollar). There may be multiple bridge assets at any given time.

- `Twitter <https://x.com/SiloFinance>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.silo.vault
