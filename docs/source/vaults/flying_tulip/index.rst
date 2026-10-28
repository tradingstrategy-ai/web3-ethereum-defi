Flying Tulip
============

`Flying Tulip <https://flyingtulip.com/ftusd/>`__ issues ftUSD and its optional
sftUSD and ssftUSD staking positions. At the reviewed blocks the sftUSD contract reports a
one-ftUSD-per-share conversion, while FT rewards are separately claimable
rather than automatically added to that conversion.

The reviewed sftUSD deployments on Ethereum, BNB Chain and Sonic can redeem
immediately when capacity is available, or enter a circuit-breaker queue. The
library therefore exposes read-only vault support and does not advertise a
generic transaction manager. FT rewards are represented historically as a
non-redeemable, explicitly flagged ftUSD share-price equivalent.

Ethereum ssftUSD is the auto-compounding variant, introduced in the
`September launch update <https://blog.flyingtulip.com/flying-tulip-september-update/>`__.
Distributed ftUSD rewards vest into the redeemable share price over eight hours
in the reviewed implementation. This is reward vesting, not a withdrawal lockup.
Unlike sftUSD, an ssftUSD withdrawal reverts atomically if underlying liquidity
or circuit-breaker capacity prevents immediate settlement; it does not return
a queued claim to the ssftUSD holder. Public transaction-manager capability
remains uncertified for both variants.

The `official address book <https://flyingtulip.com/address-book/flying-tulip-address-book-prod.json>`__
identifies the reviewed ssftUSD proxy and implementation on Ethereum. The older
ftUSD contracts API lists only sftUSD and is not a complete product registry.
ssftUSD accepts six-decimal ftUSD and issues eighteen-decimal shares. Its
historical reader uses the actual ERC-4626 conversion without external FT rewards.
Zero-address capacity reads are excluded: a zero value from that receiver does
not prove that deposits are capped. Older stored capacity observations have
this limitation; the metadata migration preserves all price history.

Both adapters retain the integration's reviewed USDC mint/redemption conversion
cost convention. Those separate costs can change and are not direct staking
fees. The documented ssftUSD reward allocation is slightly lower than sftUSD;
neither yield nor immediate liquidity is guaranteed.

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
