Solana identities
=================

Shared identity helpers support case-sensitive base58 Solana accounts,
including program-derived accounts, in the native vault data pipeline.
``SOLANA_CHAIN_ID = 9992`` is reserved internally for Solana mainnet datasets.
It is not an EVM network ID and does not select an EVM RPC reader.

``VaultSpec(9992, base58_account)`` accepts raw native input and converts it to
an existing reversible ``solana-vault-`` key before lowercase normalisation.
Raw and encoded identities have the same hash, lookup and persistence
behaviour. ``decode_solana_vault_address()`` recovers the exact original
base58 case for native APIs and explorer links. Both raw and synthetic Solana
identities are rejected when paired with an EVM chain ID.

Validation requires canonical base58 encoding of 32 bytes. It does not require
an Ed25519 signing key: legitimate program-derived accounts are off curve.
See the `Solana account documentation <https://solana.com/docs/core/accounts>`_
and the `PDA documentation <https://solana.com/docs/core/pda>`_.

Existing Neutral Trade dataset keys are unchanged, and its previous helper
imports remain compatibility exports. No stored metadata or price migration
is needed for this input-normalisation change.

.. autosummary::
   :toctree: _autosummary
   :recursive:

   eth_defi.solana
