Teller protocol API
-------------------

Teller Protocol is a decentralised lending protocol that enables long-tail
lending pools where liquidity providers can deposit assets and earn yield
from borrower interest payments.

The protocol operates with a unique architecture that separates lending and
borrowing into isolated pools with specific collateral/lending token pairs.
Each pool has pre-set terms including collateralisation ratio, APR range,
and maximum loan duration.

Key features:

- **Long-tail lending pools**: Each pool is isolated to a specific lending
  token and collateral token pair (e.g., USDC/TIBBIR)

- **Time-based loans**: All loans on Teller are time-based instead of
  price-based. Price will never cause a loan to default, only expiration

- **Liquidation auctions**: On default, collateral is transferred to a 24-hour
  Dutch auction where it is purchased to pay off the loan

- **ERC-4626 compliant**: Lenders receive vault shares representing their stake
  in the pool

- **TWAP pricing**: Uses Uniswap V3 TWAP for collateral price oracles

The protocol is built on TellerV2 which handles the core lending mechanics,
with `LenderCommitmentGroup_Pool_V2` contract providing the ERC-4626 vault interface
for pool-style lending.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/teller>`__
- `Homepage <https://www.teller.org/>`__
- `Documentation <https://docs.teller.org/teller-v2>`__
- `GitHub <https://github.com/teller-protocol/teller-protocol-v2>`__
- `Twitter <https://x.com/useteller>`__
- `Vault page <https://app.teller.org/base/earn>`__
- `Example vault on Basescan <https://basescan.org/address/0x13cd7cf42ccbaca8cd97e7f09572b6ea0de1097b>`__

.. autosummary::
   :toctree: _autosummary_teller
   :recursive:

   eth_defi.erc_4626.vault_protocol.teller.vault
