Sky API
-------

`Sky <https://sky.money/>`__ integration.

Sky (formerly MakerDAO) is one of the oldest and most established DeFi protocols.
The protocol provides the USDS stablecoin and allows users to earn yield through
staking USDS in the stUSDS vault.

The stUSDS vault is an ERC-4626 compliant tokenised vault that allows users to
stake USDS and earn the Sky Savings Rate (SSR). 

Key features:

- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the Sky Savings Rate (SSR)
- Instant deposits and withdrawals
- Fully decentralised and battle-tested infrastructure

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/sky>`__
- `Homepage <https://sky.money/>`__
- `Documentation <https://developers.sky.money/>`__
- `GitHub <https://github.com/sky-ecosystem/stusds>`__
- `Twitter <https://x.com/SkyEcosystem>`__
- `Contract on Etherscan <https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9>`__


.. autosummary::
   :toctree: _autosummary_sky
   :recursive:

   eth_defi.erc_4626.vault_protocol.sky.vault
