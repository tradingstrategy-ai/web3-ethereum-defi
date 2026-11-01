Atoma API
---------

Atoma ERC-4626 vault protocol integration.

Atoma uses the shared deposit-manager, ticket and receipt-analysis contracts.
An owner/epoch mismatch raises
:class:`eth_defi.vault.deposit_redeem.VaultRequestInvariantViolation` so an
automatic consumer cannot retry an ambiguous claim. The explicit reconciliation
methods are library tools for consumers that implement aggregate accounting;
trade-executor supports one request per trade and does not use them.

.. autosummary::
   :toctree: _autosummary_atoma
   :recursive:

   eth_defi.erc_4626.vault_protocol.atoma.vault
   eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem
