.. _gmx:

GMX API
-------

This module contains `GMX <https://gmx.io/>`__ support for Python.

# Functionality

- The functions connect directly to JSON-RPC instance and interact with GMX smart contracts
- Open and close GMX positions
- Read historical and current market data, including onchain data like open interest and volume

Tutorials
=========

- :ref:`gmx-swap` - Execute swaps on GMX
- :ref:`lagoon-gmx` - Trade GMX perpetuals from a Lagoon vault
- :ref:`gmx-ccxt-freqtrade` - Algorithmic trading on GMX using FreqTrade and CCXT


What Is GMX?
=============

GMX is a `perpetual future <https://tradingstrategy.ai/glossary/perpetual%20future>`_ (“perp”) `DEX <https://tradingstrategy.ai/glossary/DEX>`_ for `EVM <https://tradingstrategy.ai/glossary/EVM>`_ blockchains.

GMX provides perpetual trading and swaps using GM and GLV liquidity pools on
Arbitrum and Avalanche. GM is an individual market pool; GLV is a pool that
allocates liquidity across GM markets with the same backing tokens. Liquidity
providers receive a share of protocol fees and bear the pool's market,
liquidity and trader profit-and-loss exposure. See the `GMX liquidity
documentation <https://docs.gmx.io/docs/providing-liquidity/>`__ for the
current pool mechanics.

GM and GLV tokens are ERC-20 liquidity-provider shares, not ERC-4626 vaults.
Their USD value depends on pool state, total supply, capped trader PnL and
oracle prices. The GMX vault catalogue in this library gives each product a
unique name and uses USDC as its display denomination; it does not treat that
label as an onchain USDC NAV or publish a historical price curve. Exact
valuation requires the inputs documented by `GMX <https://docs.gmx.io/docs/api/gm-glv-prices/>`__.

GMX V1 and its GLP pool are archived: `GMX documents <https://docs.gmx.io/docs/category/archived/>`__
that V1 trading has been disabled since July 2025 and GLP no longer provides
liquidity. The V2 GM and GLV products covered by this catalogue are separate
from that archived deployment.

More info
=========

- `GMX documentation <https://docs.gmx.io/>`__
- `GMX Freqtrade and CCXT integration <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__ - trade GMX perpetuals using Freqtrade and CCXT

.. autosummary::
   :toctree: _autosummary_gmx
   :recursive:

   eth_defi.gmx.api
   eth_defi.gmx.base
   eth_defi.gmx.config
   eth_defi.gmx.constants
   eth_defi.gmx.market_depth
   eth_defi.gmx.contracts
   eth_defi.gmx.data
   eth_defi.gmx.events
   eth_defi.gmx.gas_utils
   eth_defi.gmx.keys
   eth_defi.gmx.order
   eth_defi.gmx.retry
   eth_defi.gmx.synthetic_tokens
   eth_defi.gmx.testing
   eth_defi.gmx.trading
   eth_defi.gmx.types
   eth_defi.gmx.utils
   eth_defi.gmx.valuation
   eth_defi.gmx.vault
   eth_defi.gmx.vault_catalog
   eth_defi.gmx.vault_sync
   eth_defi.gmx.cache
   eth_defi.gmx.gas_monitor
   eth_defi.gmx.order_tracking
   eth_defi.gmx.price_sanity
   eth_defi.gmx.verification
   eth_defi.gmx.whitelist
   eth_defi.gmx.ccxt
   eth_defi.gmx.core
   eth_defi.gmx.freqtrade
   eth_defi.gmx.graphql
   eth_defi.gmx.lagoon
   eth_defi.gmx.onchain
