3Jane API
---------

`3Jane <https://www.3jane.xyz/>`__ integration.

3Jane is a credit money market on Ethereum. Depositors supply USDC and receive
the protocol's ERC-4626 vault tokens: ``USD3``, the senior tranche, or, by
staking ``USD3``, ``sUSD3``, the junior tranche that absorbs losses first.
Pooled capital is lent as uncollateralised USDC credit lines to crypto-native
borrowers and through funding facilities for U.S. fintech lenders, including
a forward-flow agreement with Settle for inventory financing loans to U.S.
consumer goods brands.

Yield is internalised in the share price: ``USD3`` appreciates against USDC as
interest accrues, while ``sUSD3`` captures a higher proportion of pool yield in
exchange for absorbing losses first in the senior/junior waterfall. As the
protocol operates its own vaults, 3Jane acts as their curator. Both tranches
carry the maintained strategy tags in
:py:mod:`eth_defi.erc_4626.vault_protocol.threejane.tags`, including
:py:attr:`eth_defi.vault.strategy_tag.StrategyTag.inventory_financing`.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/3jane>`__
- `Homepage <https://www.3jane.xyz/>`__
- `Documentation <https://docs.3jane.xyz/>`__
- `Twitter <https://x.com/3janexyz>`__
- `USD3 contract <https://etherscan.io/address/0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc>`__
- `sUSD3 contract <https://etherscan.io/address/0xf689555121e529Ff0463e191F9Bd9d1E496164a7>`__
- `Settle forward-flow announcement <https://x.com/3janexyz/status/2117624841050411120>`__

.. autosummary::
   :toctree: _autosummary_threejane
   :recursive:

   eth_defi.erc_4626.vault_protocol.threejane.vault
   eth_defi.erc_4626.vault_protocol.threejane.tags
