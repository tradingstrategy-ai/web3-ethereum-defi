Atoma API
---------

`Atoma <https://app.atoma.fi/>`__ integration.

Atoma is a delta-neutral USDC vault on Arbitrum. Depositors receive Atoma Vault
Share tokens, while the protocol allocates capital to perpetual DEX strategies
across venues such as Nado and Extended.

Atoma uses standard ERC-4626 deposits, but disables direct ERC-4626 withdrawals.
Redemptions use an epoch-based request-and-claim flow through
``requestWithdrawal(shares)`` and ``claimWithdrawal(epochId)``.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/atoma>`__
- `App <https://app.atoma.fi/>`__
- `Twitter <https://x.com/atoma_fi>`__
- `Proxy vault <https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c>`__
- `Verified implementation <https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF>`__

.. autosummary::
   :toctree: _autosummary_atoma
   :recursive:

   eth_defi.erc_4626.vault_protocol.atoma.vault
