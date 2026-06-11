3Jane API
---------

`3Jane <https://www.3jane.xyz/>`__ integration.

3Jane is a decentralised, credit-based money market on Ethereum that facilitates
uncollateralised stablecoin lending. Depositors supply USDC and receive the
protocol's ERC-4626 vault tokens — ``USD3`` (the senior tranche) or, by staking
``USD3``, ``sUSD3`` (the junior tranche). Pooled capital is lent across
uncollateralised USDC credit lines to crypto-native borrowers and funding
conduits to U.S. fintech lenders.

Yield is internalised in the share price: ``USD3`` appreciates against USDC as
interest accrues, while ``sUSD3`` captures a higher proportion of pool yield in
exchange for absorbing losses first in the senior/junior waterfall. As the
protocol operates its own vaults, 3Jane acts as their curator.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/3jane>`__
- `Homepage <https://www.3jane.xyz/>`__
- `Documentation <https://docs.3jane.xyz/>`__
- `Twitter <https://x.com/3janexyz>`__
- `USD3 contract <https://etherscan.io/address/0x056B269Eb1f75477a8666ae8C7fE01b64dD55eCc>`__
- `sUSD3 contract <https://etherscan.io/address/0xf689555121e529Ff0463e191F9Bd9d1E496164a7>`__

.. autosummary::
   :toctree: _autosummary_threejane
   :recursive:

   eth_defi.erc_4626.vault_protocol.threejane.vault
