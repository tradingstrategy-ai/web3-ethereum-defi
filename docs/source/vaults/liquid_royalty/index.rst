Liquid Royalty API
------------------

`Liquid Royalty <https://www.liquidroyalty.com>`__ is a vault protocol on
`Berachain <https://www.berachain.com>`__ implementing a tiered vault architecture
with profit spillover and cascading backstop mechanisms.

The protocol features several vault types:

- **Senior vault**: Primary yield-generating vault
- **Junior vault**: Receives 80% of Senior's excess profits (spillover) and provides secondary backstop protection
- **ALAR SailOut Royalty vault**: Uses USDe as the underlying asset

Key features:

- Profit spillover mechanism from Senior to Junior vault
- Cascading backstop architecture
- Non-rebasing ERC-4626 share tokens
- 7-day cooldown period for penalty-free withdrawals
- 20% penalty for early withdrawals without cooldown
- 1% base withdrawal fee on Junior vault

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/liquid-royalty>`__
- `Homepage <https://www.liquidroyalty.com>`__
- `Documentation <https://docs.liquidroyalty.com>`__
- `Github <https://github.com/stratosphere-network/LiquidRoyaltyContracts>`__
- `Twitter <https://x.com/liquidroyaltyX>`__
- `Fees <https://docs.liquidroyalty.com/token/staking-product-farm>`__

.. autosummary::
   :toctree: _autosummary_liquid_royalty
   :recursive:

   eth_defi.erc_4626.vault_protocol.liquid_royalty.vault
