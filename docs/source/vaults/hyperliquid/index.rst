Hyperliquid API
---------------

`Hyperliquid <https://hyperliquid.xyz>`__ integration.

Hyperliquid vaults are core primitives within HyperCore that enable strategy
deployment using the same infrastructure as the Hyperliquid DEX, including
liquidation mechanisms and high-frequency trading capabilities.

Anyone can deposit into a vault to earn a share of the profits from the vault
leader's trading strategy. Vaults can be managed by individual traders or
operated as automated market makers (e.g. the Hyperliquidity Provider, HLP).

All strategies carry their own risk profile. Users should conduct due diligence
on vault performance history before depositing.

Key features:

- Vault leaders deploy perpetual trading strategies on HyperCore
- Depositors earn 90% of profits; vault leaders receive 10% profit share
- Protocol vaults (e.g. HLP) have no fees or profit sharing
- Same infrastructure as the DEX — liquidation engine, high-frequency execution
- All vaults denominated in USDC

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/hyperliquid>`__
- `Homepage <https://hyperliquid.xyz>`__
- `App <https://app.hyperliquid.xyz/vaults>`__
- `Documentation <https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults>`__
- `GitHub <https://github.com/hyperliquid-dex>`__
- `Twitter <https://x.com/HyperliquidX>`__
- `DefiLlama <https://defillama.com/protocol/hyperliquid-perp>`__


.. autosummary::
   :toctree: _autosummary_hyperliquid
   :recursive:

   eth_defi.hyperliquid.vault
   eth_defi.hyperliquid.vault_data_export
   eth_defi.hyperliquid.daily_metrics
