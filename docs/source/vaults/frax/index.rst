Frax API
--------

`Frax <https://frax.com/>`__ integration.

Frax is a decentralised finance protocol that originated as the first fractional-algorithmic stablecoin.
The protocol has evolved into a comprehensive DeFi ecosystem encompassing stablecoins (frxUSD),
liquid staking derivatives (frxETH/sfrxETH), lending markets (Fraxlend), an automated market maker (Fraxswap),
and its own Layer 2 chain (Fraxtal).

Fraxlend is the lending component of Frax Finance, providing isolated lending pairs where lenders deposit assets
and earn interest from borrowers. Each Fraxlend pair is an ERC-4626 compatible vault. The protocol takes 10%
of interest revenue as a fee, which is internalised in the share price.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/frax-finance>`__
- `Homepage <https://frax.com/>`__
- `Documentation <https://docs.frax.finance/>`__
- `Fraxlend documentation <https://docs.frax.finance/fraxlend/fraxlend-overview>`__
- `GitHub <https://github.com/FraxFinance>`__
- `Fraxlend smart contracts <https://github.com/FraxFinance/fraxlend>`__
- `Twitter <https://x.com/fraxfinance>`__
- `DefiLlama <https://defillama.com/protocol/frax-finance>`__
- `Audits <https://docs.frax.finance/other/audits>`__

.. autosummary::
   :toctree: _autosummary_frax
   :recursive:

   eth_defi.erc_4626.vault_protocol.frax.vault
