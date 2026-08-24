Flying Tulip API
================

Flying Tulip sftUSD support is exposed through the shared ERC-4626 vault
classification API. The adapter recognises the reviewed Ethereum, BNB Chain and
Sonic deployments and retains the contractual one-ftUSD-per-share conversion
for live reads. Its historical reader publishes a separately flagged,
non-redeemable FT-reward-equivalent series.

See :doc:`the Flying Tulip vault documentation </vaults/flying_tulip/index>`
for product, risk and redemption references.

.. autosummary::
   :toctree: _autosummary_flying_tulip
   :recursive:

   eth_defi.erc_4626.vault_protocol.flying_tulip.constants
   eth_defi.erc_4626.vault_protocol.flying_tulip.historical_context
   eth_defi.erc_4626.vault_protocol.flying_tulip.reward_price
   eth_defi.erc_4626.vault_protocol.flying_tulip.vault
