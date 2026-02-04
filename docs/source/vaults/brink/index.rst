Brink API
---------

`Brink <https://brink.money/>`__ integration.

Brink is a DeFi protocol that provides yield-bearing vaults on Mantle and other chains.
The protocol focuses on delivering sustainable yield through optimised DeFi strategies
while maintaining simplicity for users.

Brink vaults are ERC-4626 compliant but use modified events (``DepositFunds`` and
``WithdrawFunds``) instead of the standard ERC-4626 ``Deposit`` and ``Withdraw`` events.
Fees are internalised in the share price with no explicit fee getter functions exposed on-chain.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/brink>`__
- `Homepage <https://brink.money/>`__
- `App <https://brink.money/app>`__
- `Documentation <https://doc.brink.money/>`__
- `Twitter <https://x.com/BrinkDotMoney>`__
- `Audit (Halborn) <https://www.halborn.com/audits/lendle/brink-a73cf0>`__
- `Example vault on Mantle <https://mantlescan.xyz/address/0xE12EED61E7cC36E4CF3304B8220b433f1fD6e254>`__

.. autosummary::
   :toctree: _autosummary_brink
   :recursive:

   eth_defi.erc_4626.vault_protocol.brink.vault
