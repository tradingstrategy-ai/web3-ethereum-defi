Asseto API
==========

Read-only Asseto tokenised fund support.

AoABT is integrated through :class:`eth_defi.vault.base.VaultBase`, rather
than ERC-4626. The adapter combines token supply with Asseto's published
on-chain NAV/share value. The off-chain client provides optional product
metadata enrichment from Asseto's public web-application API.
It also exposes public partner roles through
:meth:`eth_defi.tokenised_fund.asseto.vault.AssetoVault.fetch_roles` for curator attribution.

.. autosummary::
   :toctree: _autosummary_asseto
   :recursive:

   eth_defi.tokenised_fund.asseto.vault
   eth_defi.tokenised_fund.asseto.historical
   eth_defi.tokenised_fund.asseto.constants
   eth_defi.tokenised_fund.asseto.offchain_api
   eth_defi.tokenised_fund.asseto.backfill
