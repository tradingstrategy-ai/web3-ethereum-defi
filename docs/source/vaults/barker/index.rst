Barker API
==========

`Barker <https://barker.money/>`__ is a stablecoin-yield discovery and routing
platform. Its public application lists opportunities and provides wallet-based
access to selected destinations.

This integration identifies the reviewed Barker H1 USDC vault on HyperEVM by
address. The deployment has an epoch-based deposit and redemption process and
an implementation not verified by the chain explorer, so it supports read-only
data only; no generic transaction adapter is advertised.

- `Homepage <https://barker.money/>`__
- `Application <https://app.barker.money/>`__
- `H1 vault <https://hyperevmscan.io/address/0x54251e24e7e5dfc66c02ea02f41bcb2419380bad>`__
- `X <https://x.com/BarkerMoneyX>`__

.. autosummary::
   :toctree: _autosummary_barker

   eth_defi.erc_4626.vault_protocol.barker.vault
