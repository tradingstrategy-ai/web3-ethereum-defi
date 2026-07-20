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

Frax also operates sFRAX and sfrxUSD stablecoin staking vaults. These products distribute protocol yield through
an increasing share redemption value, have no time lock and do not charge explicit vault-level management,
performance, deposit or withdrawal fees. Because Fraxlend and staking have different economics, the integration
uses separate :py:class:`~eth_defi.erc_4626.vault_protocol.frax.vault.FraxlendPairVault` and
:py:class:`~eth_defi.erc_4626.vault_protocol.frax.vault.FraxStakingVault` readers while reporting both as Frax.

Fraxlend candidates are discoverable from the deployer's ``LogDeploy`` event and the standard ``Deposit`` event
emitted when a new pair is seeded. Runtime classification uses the pair's immutable ``DEPLOYER_ADDRESS()``
accessor as a single contract probe and checks its result against the event-derived Frax deployers. The reviewed
staking deployments use address-based routing because their linear-reward implementation is not unique to Frax.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/frax-finance>`__
- `Homepage <https://frax.com/>`__
- `Documentation <https://docs.frax.finance/>`__
- `Fraxlend documentation <https://docs.frax.finance/fraxlend/fraxlend-overview>`__
- `sFRAX documentation <https://docs.frax.finance/frax-v3-100-cr-and-more/sfrax>`__
- `sfrxUSD documentation <https://docs.frax.com/protocol/assets/frxusd/sfrxusd>`__
- `GitHub <https://github.com/FraxFinance>`__
- `Fraxlend smart contracts <https://github.com/FraxFinance/fraxlend>`__
- `Twitter <https://x.com/fraxfinance>`__
- `DefiLlama <https://defillama.com/protocol/frax-finance>`__
- `Audits <https://docs.frax.finance/other/audits>`__

.. autosummary::
   :toctree: _autosummary_frax
   :recursive:

   eth_defi.erc_4626.vault_protocol.frax.vault
   eth_defi.erc_4626.vault_protocol.frax.constants
