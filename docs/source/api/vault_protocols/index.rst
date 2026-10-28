Vault database migrations
-------------------------

Explicit, numbered repairs for persisted vault data. Preview and apply require
the looping scanner to be stopped, including between scan cycles.

The `migration guide <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/migration-scripts.md>`__
describes authoring, production operation and journal recovery.

.. autosummary::
   :toctree: _autosummary_vault_protocols
   :recursive:

   eth_defi.vault_protocols.migration
   eth_defi.vault.scanner_lock
   eth_defi.vault_protocols.repairs.flying_tulip
   eth_defi.vault_protocols.repairs.nova
   eth_defi.vault_protocols.repairs.gtrade
   eth_defi.vault_protocols.repairs.alphaping
   eth_defi.vault_protocols.repairs.arcadia
   eth_defi.vault_protocols.repairs.strategy_tags
   eth_defi.vault_protocols.repairs.descriptions
