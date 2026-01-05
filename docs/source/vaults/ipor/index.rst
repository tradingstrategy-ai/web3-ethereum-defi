IPOR protocol API
-----------------

`IPOR Fusion <https://www.ipor.io/ipor-fusion>`__ integration.

IPOR Fusion is a meta DeFi aggregation, execution and intelligence engine that introduces
a unified liquidity framework for on-chain asset management. Fusion combines various aggregation
and routing protocols into a single smart contract layer, automating asset management and
maximising returns across yield sources.

Vaults in IPOR Fusion are called Plasma Vaults and are the central part of the Fusion system.
All Plasma Vaults implement the ERC-4626 standard. Plasma Vaults implement a Diamond Proxy
pattern and delegate calls to fuses and attached contracts to manage fees and rewards collection.

Key features:

- Intelligence-driven execution for DeFi operations including looping, carry trades and arbitrage
- Single integration point for accessing multiple yield venues
- Automated rebalancing, optimisation and risk management
- Siloed risk exposure between Fusion vaults (losses not socialised)
- Built with battle-tested smart contracts and audited by top-tier firms

Links
~~~~~

- `Homepage <https://www.ipor.io/>`__
- `App <https://app.ipor.io/>`__
- `Documentation <https://docs.ipor.io/ipor-fusion/vaults>`__
- `GitHub <https://github.com/IPOR-Labs/ipor-fusion>`__
- `Twitter <https://x.com/ipor_io>`__
- `DefiLlama <https://defillama.com/protocol/ipor-fusion>`__

.. autosummary::
   :toctree: _autosummary_ipor
   :recursive:

   eth_defi.erc_4626.vault_protocol.ipor.vault
