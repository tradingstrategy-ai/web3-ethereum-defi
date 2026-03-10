LI.FI API
---------

Introduction
============

`LI.FI <https://li.fi>`__ is a cross-chain bridge and DEX aggregator that enables
token transfers and swaps across multiple EVM chains. It aggregates liquidity from
various bridges and DEXes to find optimal routes for cross-chain transfers.

``eth_defi`` provides LI.FI integration for cross-chain gas feeding — automatically
topping up native gas token balances on multiple chains from a single source chain.

For a complete working example, see the :ref:`lifi-feed-crosschain` tutorial.

Use cases:

- **Cross-chain gas feeding**: Monitor gas balances across chains and bridge native
  tokens when any chain runs low
- **Multi-chain hot wallet management**: Keep hot wallets funded across all operational chains
- **Automated bridging**: Programmatic cross-chain native token transfers

Bridge flow:

1. Check native token balances on target chains
2. Fetch native token USD prices from LI.FI token API
3. Identify chains below the minimum gas threshold
4. Fetch bridge quotes from LI.FI (GET /v1/quote)
5. Sign and broadcast bridge transactions on the source chain

Links:

- `LI.FI documentation <https://docs.li.fi>`__
- `LI.FI API reference <https://docs.li.fi/api-reference/get-a-quote-for-a-token-transfer>`__
- `LI.FI partner portal <https://portal.li.fi>`__ (for API keys)

.. autosummary::
   :toctree: _autosummary_lifi
   :recursive:

   eth_defi.lifi.api
   eth_defi.lifi.constants
   eth_defi.lifi.quote
   eth_defi.lifi.crosschain
   eth_defi.lifi.top_up
   eth_defi.lifi.intents
