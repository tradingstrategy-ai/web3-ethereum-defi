sBOLD API
---------

`sBOLD <https://www.k3.capital/>`__ integration.

sBOLD is a yield-bearing tokenised representation of deposits into Liquity V2 Stability Pools.
It allows users to deposit BOLD tokens across multiple stability pools (wstETH, rETH, wETH) and
receive ERC-4626 vault shares representing their position. The protocol serves as a passive
non-custodial savings account for BOLD token holders.

The vault earns yield through two mechanisms:

1. Interest distributions from borrowers paid to stability pools
2. Liquidation penalties through automated collateral swaps, eliminating direct price exposure

Key features:

- Entry fee is configurable (initially 0%)
- Swap fees are configurable (initially 0%)
- Automatic rebalancing across stability pools
- Audited by ChainSecurity

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/sbold>`__
- `Homepage <https://www.k3.capital/>`__
- `Documentation <https://k3capital.substack.com/>`__
- `GitHub <https://github.com/K3Capital/sBOLD>`__
- `Twitter <https://x.com/k3_capital>`__
- `Audit <https://www.chainsecurity.com/security-audit/k3-sbold>`__
- `Contract on Etherscan <https://etherscan.io/address/0x50bd66d59911f5e086ec87ae43c811e0d059dd11>`__


.. autosummary::
   :toctree: _autosummary_sbold
   :recursive:

   eth_defi.erc_4626.vault_protocol.sbold.vault
