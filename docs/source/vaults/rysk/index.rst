Rysk Premium
============

`Rysk Premium <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__
offers epoch-settled option liquidity pools. Its LP shares are ERC-20 tokens,
but do not use the ERC-4626 interface.

The adapter is read-only because subscriptions and withdrawals are queued and
settle at protocol epoch boundaries. Its collateral-denominated equity curve
uses the final ``withdrawalPps`` selected from ``EpochPriceSet`` and
``EpochPriceDisputed`` events when ``epochExecuted`` finalises the epoch. It
does not turn ``getTVL()`` into NAV: that value is free plus allocated
collateral, whereas full NAV also includes the marked option-book liability.

Pools are discovered from their protocol-specific ``EpochPriceSet`` event and
confirmed using Rysk-specific onchain function probes. Final epoch
observations are streamed through Hypersync, their block times use the shared
timestamp cache, and the results are stored in the shared contextual-history
DuckDB. The application catalogue is used only by manual operator scripts.

.. autosummary::
   :toctree: _autosummary_rysk
   :recursive:

   eth_defi.erc_4626.vault_protocol.rysk.vault
   eth_defi.erc_4626.vault_protocol.rysk.api
   eth_defi.erc_4626.vault_protocol.rysk.historical
   eth_defi.erc_4626.vault_protocol.rysk.historical_context
