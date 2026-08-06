Nest
====

`Nest <https://www.nest.credit/>`__ is Plume's real-world-asset vault
infrastructure. Its products provide routes for stablecoin deposits and
redemptions into tokenised yield strategies. A product can have several chain
and denomination routes, while its share token remains separate from the
chain-specific ``NestVault`` entrypoint.

NestVault combines ERC-4626 accounting with ERC-7540 asynchronous redemptions
and ERC-7575 separate-share-token support. The adapter detects Nest's published
deployments using the chain-restricted ``operatorRegistry()`` view and exposes
their aggregate queued-share balance through ``totalPendingShares()``. A
redemption request may need compliance checks and a later claim, so the
integration deliberately does not advertise a generic deposit manager.

The adapter joins Nest's public `contract catalogue
<https://api.nest.credit/v1/vaults?status=all>`__ with its public CMS for
strategy descriptions, live statistics and the estimated redemption duration.
These are cached advisory metadata; the contract state remains authoritative
for a transaction.

The representative nOPAL USDC route is a verified
`Avalanche NestVault <https://snowtrace.io/address/0xd258029cf5a177e3306e09fbea63424543a505c0#code>`__.

.. autosummary::
   :toctree: _autosummary_nest
   :recursive:

   eth_defi.erc_4626.vault_protocol.nest.vault
   eth_defi.erc_4626.vault_protocol.nest.offchain_metadata
