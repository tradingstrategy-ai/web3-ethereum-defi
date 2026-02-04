Liquidity Royalty Tranching API
-------------------------------

Liquidity Royalty Tranching is a protocol implementing a tiered vault system on Berachain with profit spillover and cascading backstop mechanisms.

The protocol features three vault types:

- **Senior vault**: Primary yield-generating vault
- **Junior vault**: Receives 80% of Senior's excess profits (spillover) and provides secondary backstop protection
- **Reserve vault**: Provides primary backstop protection

Key features:

- Profit spillover mechanism from Senior to Junior vault
- Cascading backstop architecture (Reserve → Junior → Senior)
- Non-rebasing ERC-4626 share tokens
- 7-day cooldown period for penalty-free withdrawals
- 20% penalty for early withdrawals without cooldown, plus 1% base withdrawal fee

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/liquidity-royalty-tranching>`__
- `Homepage <https://github.com/stratosphere-network/LiquidRoyaltyContracts>`__
- `Documentation <https://github.com/stratosphere-network/LiquidRoyaltyContracts/tree/master/docs>`__
- `Github <https://github.com/stratosphere-network/LiquidRoyaltyContracts>`__

.. autosummary::
   :toctree: _autosummary_liquidity_royalty
   :recursive:

   eth_defi.erc_4626.vault_protocol.liquidity_royalty.vault
