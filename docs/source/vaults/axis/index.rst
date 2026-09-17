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
and a request, cooldown, servicing and claim process for V2 redemptions. The V2
default cooldown is configurable, account-specific policies may differ, and a
matured request still depends on servicing. Plasma V1 is not ERC-7540: it uses
direct ERC-4626 redemption while its configurable cooldown is zero, and its
separate cooldown and unstake flow when the setting is non-zero. The adapter
reads this setting from contract state instead of applying one duration to both
deployments.

The reviewed contracts do not deduct explicit management, performance, deposit
or withdrawal fees. Reward vesting changes the sUSDx exchange rate and should
not be described as fee skimming. Axis says that rewards are discretionary,
not a fixed return.

Axis documents its product risks and `audit reports <https://docs.axis.to/backing-reserves-and-transparency/audits>`__.
The library classifies both reviewed contracts through chain-aware hardcoded
addresses. It does not yet advertise a transaction manager because the complete
V2 asynchronous lifecycle and the V1 mode-dependent lifecycle have not both
been certified in the library.

.. autosummary::
   :toctree: _autosummary_axis
   :recursive:

   eth_defi.erc_4626.vault_protocol.axis.constants
   eth_defi.erc_4626.vault_protocol.axis.vault
