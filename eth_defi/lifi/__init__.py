"""LI.FI cross-chain bridge and swap aggregator integration.

`LI.FI <https://li.fi>`__ is a cross-chain bridge and DEX aggregator
that enables token transfers and swaps across multiple EVM chains.

This module provides functionality for:

- Cross-chain native gas token bridging
- Checking gas balances across multiple chains
- Automated gas top-up for hot wallets

For more information see `LI.FI API documentation <https://docs.li.fi>`__.

Key components:

- :py:mod:`eth_defi.lifi.constants` - API configuration and defaults
- :py:mod:`eth_defi.lifi.api` - API helpers and error handling
- :py:mod:`eth_defi.lifi.quote` - Cross-chain quote fetching
- :py:mod:`eth_defi.lifi.crosschain` - Cross-chain gas feeding
"""
