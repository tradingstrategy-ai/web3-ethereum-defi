Dolomite API
------------

`Dolomite <https://dolomite.io/>`__ integration.

Dolomite is a next-generation DeFi lending and borrowing platform built on Arbitrum
that supports over 1,000 unique assets with capital-efficient money markets. The protocol
allows users to lend, borrow, trade, margin, and hedge various crypto assets with greater
capital efficiency and broader asset support than traditional protocols.

Dolomite ERC-4626 vaults wrap user margin positions into standard ERC-4626 tokenised
vault shares, enabling seamless integration with other DeFi protocols. These vaults
support features like collateralisation for borrowing and integration with external
yield-bearing assets.

Key features:

- Yield accrues from lending in Dolomite money markets
- No explicit deposit/withdrawal fees at the vault level
- Fees are internalised through interest rate spreads
- Instant deposits and withdrawals (subject to market liquidity)
- Chainlink Automation secures price updates for vault assets

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/dolomite>`__
- `Homepage <https://dolomite.io/>`__
- `Application <https://app.dolomite.io/>`__
- `Documentation <https://docs.dolomite.io/>`__
- `GitHub <https://github.com/dolomite-exchange/dolomite-margin>`__
- `Twitter <https://twitter.com/dolomite_io>`__
- `DefiLlama <https://defillama.com/protocol/dolomite>`__
- `Audits <https://docs.dolomite.io/introduction/security-and-audits>`__
- `Example contract on Arbiscan (dUSDC) <https://arbiscan.io/address/0x444868b6e8079ac2c55eea115250f92c2b2c4d14>`__
- `Example contract on Arbiscan (dUSDT) <https://arbiscan.io/address/0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8>`__


.. autosummary::
   :toctree: _autosummary_dolomite
   :recursive:

   eth_defi.erc_4626.vault_protocol.dolomite.vault
