.. _gmx:

GMX API
-------

This module contains `GMX <https://gmx.io/>`__ support for Python.

Functionality
=============

- The functions connect directly to JSON-RPC instance and interact with GMX smart contracts
- Open and close GMX positions
- Read historical and current market data, including onchain data like open interest and volume

Tutorials
=========

- :ref:`gmx-swap` - Execute swaps on GMX
- :ref:`lagoon-gmx` - Trade GMX perpetuals from a Lagoon vault
- :ref:`gmx-ccxt-freqtrade` - Algorithmic trading on GMX using FreqTrade and CCXT


What is GMX?
=============

GMX is a `perpetual future <https://tradingstrategy.ai/glossary/perpetual%20future>`_ (“perp”) `DEX <https://tradingstrategy.ai/glossary/DEX>`_ for `EVM <https://tradingstrategy.ai/glossary/EVM>`_ blockchains.

GMX offers dozens of perp trading pairs for popular cryptocurrencies like BTC, ETH and SOL. GMX is so-called pure `onchain <https://tradingstrategy.ai/glossary/onchain>`_ market with high degree of decentralisation. Thus, GMX has high `composability <https://tradingstrategy.ai/glossary/composability>`_ with other `decentralised finance <https://tradingstrategy.ai/glossary/decentralised%20finance>`_ `protocols <https://tradingstrategy.ai/glossary/protocols>`_. This allows users `longing <https://tradingstrategy.ai/glossary/longing>`_ and `shorting <https://tradingstrategy.ai/glossary/shorting>`_ different asset prices with `leverage <https://tradingstrategy.ai/glossary/leverage>`_ onchain.

This GM/GLV vault dataset integration supports Arbitrum and Avalanche.

GMX V2 has individual GM pools and multi-market GLVs. GM tokens represent
shares of one isolated market. GLV tokens represent shares of a vault holding
compatible GM tokens; liquidity can be shifted between supported markets based
on utilisation and risk recommendations. GLP belongs to GMX V1 and is not
covered by this vault reader.

There are multiple third-party DeFi vaults built on the top of GMX, like `Umami’s GM vaults <umami.finance>`_ following `ERC-4626 <https://tradingstrategy.ai/glossary/ERC-4626>`_ standard.

GMX V2 uses oracle prices and isolated pools rather than an order book or the
V1 GLP model. `Liquidity providers
<https://tradingstrategy.ai/glossary/liquidity%20providers>`_ are exposed to
the deposited token inventory, trader profit and loss, borrowing and trading
fees, funding mechanics, price impact and protocol risk. GM and GLV returns
are not stable or guaranteed. See the official `liquidity and risk explanation
<https://docs.gmx.io/docs/providing-liquidity/>`__.

Liquidity-provider vault data
=============================

The vault dataset enumerates GM and GLV tokens from the GMX V2 Reader
contracts on Arbitrum and Avalanche. Historical USD performance comes from
deposit-context ``MarketPoolValueUpdated`` and ``GlvValueUpdated`` events:

.. code-block:: text

   share price equivalent = event USD value / corresponding event token supply

For GM, the reader accepts only post-deposit ``MarketPoolValueUpdated`` rows.
GMX values deposits and withdrawals with different PnL-factor and
maximise/minimise settings, so mixing their observations would create false
returns. For GLV, ``GlvValueUpdated`` records value and supply after execution;
GLV shares are minted or burned proportionally using the pre-flow ratio.
Dividing matched value and supply prevents flow size alone from appearing as
profit. GMX notes that GLV values may omit shift, deposit or withdrawal fees
when a GLV oracle price is used, so the curve remains approximate.

This is an event-observed share-price equivalent, not a continuously sampled
canonical NAV. Sparse observations pass through the same change threshold,
Parquet writer and metric calculations as other EVM vaults. The common metric
path forward fills the last observed value to a daily index. An event-free day
is therefore assigned a zero return and the complete intervening movement is
assigned to the next observed event day. This is an accepted approximation
that makes all common performance metrics available.

Return and CAGR retain their endpoint interpretation. Volatility and Sharpe
describe the approximated daily path and therefore depend on operation-event
cadence; they are not continuously sampled GMX NAV statistics.

The result is a USD-denominated GMX share curve. It approximates a single-sided
USDC deposit only where USDC is accepted and does not model the deposit
transaction. Accepted deposit tokens are product-specific. Execution fees,
price impact, token spreads, deposit or withdrawal fees, and waiting time are
not deducted. GMX has no depositor-facing management or performance fee, so
the generic vault fee interface reports those two fee types as zero.

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
   eth_defi.gmx.cache
   eth_defi.gmx.gas_monitor
   eth_defi.gmx.historical_context
   eth_defi.gmx.historical_oracle
   eth_defi.gmx.order_tracking
   eth_defi.gmx.price_sanity
   eth_defi.gmx.verification
   eth_defi.gmx.vault
   eth_defi.gmx.vault_catalog
   eth_defi.gmx.vault_sync
   eth_defi.gmx.whitelist
   eth_defi.gmx.ccxt
   eth_defi.gmx.core
   eth_defi.gmx.freqtrade
   eth_defi.gmx.graphql
   eth_defi.gmx.lagoon
   eth_defi.gmx.onchain
