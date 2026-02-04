Ethena API
----------

`Ethena <https://ethena.fi/>`__ integration.

Ethena is a synthetic dollar protocol built on Ethereum that provides a crypto-native
solution for money, USDe, alongside a globally accessible dollar savings asset, sUSDe.

USDe is a synthetic dollar backed by crypto assets and corresponding short futures
positions, maintaining its $1 peg through delta-neutral hedging. In practice, Ethena
accepts crypto collateral (such as Ethereum) and immediately takes an equivalent short
position in perpetual futures markets, balancing price movements to stabilise USDe's value.

sUSDe (staked USDe) allows users to stake USDe and earn yield from three sustainable
sources: funding and basis spread from delta hedging derivatives positions, rewards from
liquid stable backing assets, and staked ETH consensus/execution layer rewards.

Key features:

- No management or performance fees at the smart contract level
- Yield comes from protocol funding rates and staking rewards
- Governance-configurable cooldown period for withdrawals (up to 90 days)
- Fully backed by USDe

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/ethena>`__
- `Homepage <https://ethena.fi/>`__
- `Documentation <https://docs.ethena.fi/>`__
- `GitHub <https://github.com/ethena-labs>`__
- `Twitter <https://x.com/ethena_labs>`__
- `Contract on Etherscan <https://etherscan.io/address/0x9d39a5de30e57443bff2a8307a4256c8797a3497>`__


.. autosummary::
   :toctree: _autosummary_ethena
   :recursive:

   eth_defi.erc_4626.vault_protocol.ethena.vault
