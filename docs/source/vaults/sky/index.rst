Sky API
-------

`Sky <https://sky.money/>`__ integration.

Sky, formerly MakerDAO, issues the USDS stablecoin. Its Staked USDS
(stUSDS) vault lends USDS against staked SKY, Sky's governance token.

Staked USDS withdrawal liquidity
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `Staked USDS vault on Ethereum <https://tradingstrategy.ai/vaults/staked-usds>`__
has no scheduled lock-up. Withdrawals depend on the USDS available after
outstanding loans and active collateral auctions.

The withdrawal-capacity reader measures how much USDS is available at a fixed
block. The catalogue warns when fresh capacity falls below the configured share
of total assets.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/sky>`__
- `Homepage <https://sky.money/>`__
- `Documentation <https://developers.sky.money/>`__
- `GitHub <https://github.com/sky-ecosystem/stusds>`__
- `Twitter <https://x.com/SkyEcosystem>`__
- `Contract on Etherscan <https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9>`__


.. autosummary::
   :toctree: _autosummary_sky
   :recursive:

   eth_defi.erc_4626.vault_protocol.sky.vault
