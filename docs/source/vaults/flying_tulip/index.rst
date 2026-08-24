Flying Tulip
============

`Flying Tulip <https://flyingtulip.com/ftusd/>`__ issues ftUSD and its optional
sftUSD staking position. At the reviewed blocks the sftUSD contract reports a
one-ftUSD-per-share conversion, while FT rewards are separately claimable
rather than automatically added to that conversion.

The reviewed sftUSD deployments on Ethereum, BNB Chain and Sonic can redeem
immediately when capacity is available, or enter a circuit-breaker queue. The
library therefore exposes read-only vault support and does not advertise a
generic transaction manager. FT rewards are represented historically as a
non-redeemable, explicitly flagged ftUSD share-price equivalent.

Flying Tulip publishes its `product documentation <https://docs.flyingtulip.com/product-suite/ft-usd/>`__,
`security repository <https://github.com/flyingtulipdotcom/security>`__ and
`known issues <https://github.com/flyingtulipdotcom/security/blob/master/KNOWN_ISSUES.md>`__.
No public report-level audit covering the reviewed staking vault, wrapper and
circuit-breaker deployments has been identified.

.. autosummary::
   :toctree: _autosummary_flying_tulip
   :recursive:

   eth_defi.erc_4626.vault_protocol.flying_tulip.constants
   eth_defi.erc_4626.vault_protocol.flying_tulip.vault
