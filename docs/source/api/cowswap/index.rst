CowW Swap API
-------------

CoW Protocol is a meta-DEX aggregation protocol that leverages trade intents and fair combinatorial batch auctions to find users better prices for trading crypto assets.

The protocol relies on third parties known as "solvers" to find the best execution paths for trade intents — signed messages that specify conditions for executing transaction on Ethereum and EVM-compatible chains.

Upon first receiving a user intent, the protocol groups it alongside other intents in a batch. When executing trade intents, solvers first try to find a Coincidence of Wants (CoW) within the existing batch to offer an optimal price over any on-chain liquidity. If the protocol does not find a CoW, the solvers search all available on-chain and off-chain liquidity to find the best price for a set of trade intents within a batch.

Liquidity sources include:
- AMMs (e.g. Uniswap, Sushiswap, Balancer, Curve, etc.)
- DEX Aggregators (e.g. 1inch, Paraswap, Matcha, etc.)
- Private Market Makers

The wide range of liquidity that solvers tap into makes CoW Protocol a meta-DEX aggregator, or an aggregator of aggregators.

- :ref:`Read tutorial <lagoon-cowswap>`
- `CoW Swap docs <https://docs.cow.fi/cow-protocol/r>`__
- `Twitter <https://x.com/CoWSwap>`__.

.. autosummary::
   :toctree: _autosummary_cow
   :recursive:

   eth_defi.cow.api
   eth_defi.cow.constants
   eth_defi.cow.order
   eth_defi.cow.quote
   eth_defi.cow.status
