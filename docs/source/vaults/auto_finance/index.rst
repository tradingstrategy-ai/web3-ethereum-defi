AUTO Finance API
-----------------

`Auto Finance <https://www.tokemak.xyz/>`__ integration.

Also known as *Tokemak*, *Autopool protocol*.

Providing liquidity in DeFi, particularly to correlated trading pairs such as ETH LSTs/LRTs and
stablecoins on decentralised exchanges (DEXs), or to lending markets like AAVE, Fluid, and Morpho,
can be an extremely efficient onchain way to earn additional yield on one's assets. Despite this,
it comes with many complexities to achieve that efficiency.

Autopools were developed to address the many challenges liquidity providers (LPs) face when optimising
for best performance. Users deposit assets into an autopool, each representing a set of potential
deployment destinations for the autonomous rebalance logic. The Autopool Rebalance Logic takes over,
autonomously monitoring the underlying pools and leveraging its powerful framework to rebalance.

Key features:

- Autonomous LP optimisation with ERC-4626 standard vaults
- No lock-ups or cooldown periods for withdrawals
- Modular architecture allowing plug-and-play integration of new assets and destinations

Links
~~~~~

- `Homepage <https://www.tokemak.xyz/>`__
- `App <https://app.tokemak.xyz/>`__
- `Documentation <https://docs.tokemak.xyz/>`__
- `Twitter <https://x.com/autopools>`__
- `DefiLlama <https://defillama.com/protocol/tokemak>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.autopool.vault
