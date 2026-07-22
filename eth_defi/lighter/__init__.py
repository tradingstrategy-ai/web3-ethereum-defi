"""Lighter DEX protocol integration.

This module provides tools for interacting with the `Lighter <https://lighter.xyz/>`__
decentralised perpetuals exchange, including pool data extraction and daily metrics.

The metrics integration indexes public pools (vaults) from Lighter deployments
associated with Ethereum and Robinhood Chain, including protocol liquidity
pools and user-created leveraged pools. Custody and Guard helpers remain
specific to the original Ethereum deployment.

See :py:mod:`eth_defi.lighter.vault` for pool-related functionality,
:py:mod:`eth_defi.lighter.daily_metrics` for the daily metrics pipeline and
:py:mod:`eth_defi.lighter.valuation` for account NAV helpers.
"""
