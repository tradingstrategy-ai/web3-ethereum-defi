AUTO Finance API
-----------------

Also known as *Tokemak*, *Autopool protocol*.

Providing liquidity in DeFi, particularly to correlated trading pairs such as ETH LSTs/LRTs and stablecoins on decentralized exchanges (DEXs), or to lending markets like AAVE, Fluid, and Morpho, can be an extremely efficient onchain way to earn additional yield on one's assets. Despite this, it comes with many complexities to achieve that efficiency.

Autopools were developed to address the many challenges liquidity providers (LPs) face when optimizing for best performance. No protocol currently offers fully autonomous, transparent and sophisticated rebalance solution focused solely on liquidity provision.

Users deposit assets into an autopool, each representing a set of potential deployment destinations for the autonomous rebalance logic.

From there, the Autopool Rebalance Logic takes over, autonomously monitoring the underlying pools and leveraging its powerful framework to rebalance – thus abstracting the complexity associated with rebalancing, compounding, and staking of LP tokens away from the user.

- `Twitter <https://x.com/autopools>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.autopool.vault
