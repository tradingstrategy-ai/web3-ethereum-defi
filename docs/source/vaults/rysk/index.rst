Rysk Premium
============

`Rysk Premium <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__
offers epoch-settled option liquidity pools. Its LP shares are ERC-20 tokens,
but do not use the ERC-4626 interface.

The adapter is read-only because subscriptions and withdrawals are queued and
settle at protocol epoch boundaries. Its equity curve uses the public
application API's final epoch ``withdrawalPps`` as the exit-equivalent share
price. It retains ``depositPps`` for audit but does not turn dashboard TVL into
NAV: TVL is free plus allocated collateral, whereas full NAV also includes the
marked option-book liability and accrued fees.

The catalogue is refreshed before historical price scans, and its snapshots are
stored in the shared contextual-history DuckDB.

.. autosummary::
   :toctree: _autosummary_rysk
   :recursive:

   eth_defi.erc_4626.vault_protocol.rysk.vault
   eth_defi.erc_4626.vault_protocol.rysk.api
   eth_defi.erc_4626.vault_protocol.rysk.historical
   eth_defi.erc_4626.vault_protocol.rysk.historical_context
   eth_defi.erc_4626.vault_protocol.rysk.vault_sync
