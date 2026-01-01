Maple API
---------

`Maple Finance <https://maple.finance/>`__ integration.

Maple Finance is an institutional-grade DeFi lending protocol that connects
capital providers with vetted institutional borrowers. The Syrup protocol
provides permissionless access to yield-bearing tokens (syrupUSDC, syrupUSDT)
that represent deposits in Maple's institutional lending pools.

When users deposit USDC or USDT into Syrup, they receive syrup tokens in return.
These tokens are yield-bearing LP tokens, similar to Aave's aUSDC or Compound's cUSDC.
The underlying deposits are lent to vetted institutional borrowers like market makers
and trading firms, with loans secured by overcollateralised digital asset collateral.

Key features:

- Institutional yield: Access to real-world lending rates typically higher than
  standard DeFi lending (10-15% APY)
- Overcollateralised: Loans are secured by digital asset collateral (BTC, ETH)
  at ratios significantly above 100%
- Permissionless: Unlike Maple's core institutional pools, Syrup is accessible
  to anyone with a DeFi wallet
- Composability: syrup tokens are standard ERC-20 tokens that can be used in
  other DeFi applications

- `Homepage <https://maple.finance/>`__
- `App <https://app.maple.finance/earn>`__
- `Documentation <https://docs.maple.finance/>`__
- `GitHub <https://github.com/maple-labs/maple-core-v2>`__
- `Twitter <https://x.com/maplefinance>`__
- `syrupUSDC on Etherscan <https://etherscan.io/address/0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b>`__
- `syrupUSDT on Etherscan <https://etherscan.io/address/0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d>`__


.. autosummary::
   :toctree: _autosummary_maple
   :recursive:

   eth_defi.erc_4626.vault_protocol.maple.vault
