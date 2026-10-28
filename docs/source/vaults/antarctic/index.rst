Antarctic
=========

`Antarctic Exchange <https://www.antarctic.exchange/>`__ is a perpetual futures
exchange. AMLP supplies market-making liquidity and AHLP supplies hedging
liquidity on Arbitrum; both products issue ERC-20 LP tokens denominated in USDT.
See the `official liquidity description <https://docs.antarctic.exchange/antarctic-overview/liquidity-provider-protection>`__.

The adapter is read-only. Subscription execution prices come from manager
``AddLiquidity`` events, scaled using USDT and LP token metadata. These are
sparse handler-reported valuations, not independently calculated NAV. Removal
ratios are diagnostic only, and separately distributed staking income is excluded.
Queued deposit/redemption transaction construction remains unsupported.

The normal all-chain Arbitrum scanner prefills Hypersync event context before
writing the existing raw price Parquet. Cleaning and JSON publication retain
actual observation timestamps and sample counts. Both pools resolve to Antarctic
as their protocol-managed curator, sharing its protocol feeds and logo. Unknown historical supply,
fees and unmeasured position exposure remain unknown. For operational details,
see ``eth_defi/erc_4626/vault_protocol/antarctic/README-Antarctic.md``.

.. autosummary::
   :toctree: _autosummary_antarctic
   :recursive:

   eth_defi.erc_4626.vault_protocol.antarctic.vault
   eth_defi.erc_4626.vault_protocol.antarctic.historical
   eth_defi.erc_4626.vault_protocol.antarctic.historical_context
