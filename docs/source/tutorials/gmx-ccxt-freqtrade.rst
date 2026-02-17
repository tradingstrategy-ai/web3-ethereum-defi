.. meta::
   :description: How to run algorithmic trading strategies on GMX using FreqTrade and CCXT

.. _gmx-ccxt-freqtrade:

GMX, CCXT and FreqTrade
=======================

This tutorial shows how to use a `CCXT <https://tradingstrategy.ai/glossary/ccxt>`__-compatible exchange adapter
for `GMX <https://tradingstrategy.ai/glossary/gmx>`__, a decentralised `perpetual futures <https://tradingstrategy.ai/glossary/perpetual-future>`__ exchange,
with `FreqTrade <https://tradingstrategy.ai/glossary/freqtrade>`__, an `algorithmic trading <https://tradingstrategy.ai/glossary/algorithmic-trading>`__ framework for Python.

- Run automated trading strategies on GMX using FreqTrade
- Backtest strategies against historical GMX data
- Execute live trades on Arbitrum with self-custodial wallets
- Use a CCXT-compatible interface to GMX's onchain trading
- `Trading vaults <https://web3-ethereum-defi.tradingstrategy.ai/tutorials/lagoon-gmx>`__ for user-investable trading strategies and copy trading on GMX

This project is funded by an `Arbitrum DAO grant <https://tradingstrategy.ai/blog/trading-strategy-receives-arbitrum-foundation-grant-to-bring-ccxt-support-to-gmx>`__.

The adapter is provided by the `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`__ Python package,
which provides the low-level primitives for RPC, smart contract interaction and onchain data ingestion.
These are mapped to CCXT/FreqTrade transparently via `monkey patching <https://en.wikipedia.org/wiki/Monkey_patch>`__.

For the full tutorial, installation instructions, backtesting guide and live trading setup, see the
`gmx-ccxt-freqtrade repository on GitHub <https://github.com/tradingstrategy-ai/gmx-ccxt-freqtrade>`__.
