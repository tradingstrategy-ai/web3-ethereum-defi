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

GMX offers dozens of perp trading pairs for popular cryptocurrencies like BTC, ETH and SOL. GMX is so-called pure `onchain <https://tradingstrategy.ai/glossary/onchain>`_ market with high degree of decentralisation. Thus, GMX has high `composability <https://tradingstrategy.ai/glossary/composability>`_ with other `decentralised finance <https://tradingstrategy.ai/glossary/decentralised%20finance>`_ `protocols <https://tradingstrategy.ai/glossary/protocols>`_. This allows users `longing <https://tradingstrategy.ai/glossary/longing>`_ and `shorting <https://tradingstrategy.ai/glossary/shorting>`_ different asset prices with `leverage <https://tradingstrategy.ai/glossary/leverage>`_ onchain.

GMX is one of the oldest pure onchain perpetual future market places still running. GMX mainly operates on Arbitrum, but has expanded to include cross-chain functionality. `GMX saw a hacking incident in July 2025 from which it recovered <www.google.com>`_.

GMX has its own pools for `market making <https://tradingstrategy.ai/glossary/market%20making>`_ where pools users can provide liquidity and take the other side of the trade. There are `GLP vaults <x.com>`_ in GMX v2 and older GLP vaults in GMX v1. GLV is the index pool of GMX’s markets, rebalancing liquidity to its best-performing GM pools and generating fees from them. As a result, GLV offers liquidity providers a balanced instrument with stable risk-adjusted returns and high capital efficiency.

There are multiple third-party DeFi vaults built on the top of GMX, like `Umami’s GM vaults <umami.finance>`_ following `ERC-4626 <https://tradingstrategy.ai/glossary/ERC-4626>`_ standard.

GMX price formation relies on its multi-asset liquidity pool (GLP) and a “virtual” `AMM <https://tradingstrategy.ai/glossary/AMM>`_ (vAMM) model that uses Chainlink oracles for price data instead of a traditional `order book <https://tradingstrategy.ai/glossary/order%20book>`_. When users trade, the vAMM calculates the price based on the ratio of assets in the GLP pool, and price feeds from Chainlink ensure accurate pricing for fixed-price trades.

Unlike standard AMMs, GMX’s vAMM shifts risk to `liquidity providers <https://tradingstrategy.ai/glossary/liquidity%20providers>`_ (LPs) in the GLP pool, with traders paying a fee that is split between the GMX token holders and the GLP LPs.

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
   eth_defi.gmx.contracts
   eth_defi.gmx.data
   eth_defi.gmx.events
   eth_defi.gmx.execution_buffer
   eth_defi.gmx.gas_utils
   eth_defi.gmx.keys
   eth_defi.gmx.order
   eth_defi.gmx.retry
   eth_defi.gmx.synthetic_tokens
   eth_defi.gmx.testing
   eth_defi.gmx.trading
   eth_defi.gmx.types
   eth_defi.gmx.utils
   eth_defi.gmx.cache
   eth_defi.gmx.gas_monitor
   eth_defi.gmx.order_tracking
   eth_defi.gmx.price_sanity
   eth_defi.gmx.verification
   eth_defi.gmx.whitelist
   eth_defi.gmx.ccxt
   eth_defi.gmx.lagoon

