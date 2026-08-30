Axis
====

`Axis <https://www.axis.to/>`__ issues USDx, a synthetic dollar, and sUSDx,
the rewards-vault position obtained by staking USDx. Axis describes its strategy
as market-neutral arbitrage across venues, assets and currencies.

The reviewed deployments are the current Ethereum V2
`StakedUSDx contract <https://etherscan.io/address/0xEB892628D1E58BC475A6dCB7F5dBC4F591632AA4>`__
and the legacy Plasma V1
`Staked Axis USD contract <https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012>`__.
Both issue sUSDx shares for USDx deposits. Axis documents immediate deposits
and a request, cooldown, servicing and claim process for V2 redemptions. Its
published documentation says that rewards vest into the vault and that they
are discretionary, not a fixed return.

Axis documents its product risks and `audit reports <https://docs.axis.to/backing-reserves-and-transparency/audits>`__.
The library classifies both reviewed contracts through chain-aware hardcoded
addresses. It does not yet advertise a transaction manager because the complete
asynchronous redemption lifecycle has not been certified in the library.

.. autosummary::
   :toctree: _autosummary_axis
   :recursive:

   eth_defi.erc_4626.vault_protocol.axis.constants
   eth_defi.erc_4626.vault_protocol.axis.vault
