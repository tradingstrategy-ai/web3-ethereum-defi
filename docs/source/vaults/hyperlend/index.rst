Hyperlend API
-------------

`Hyperlend <https://hyperlend.finance/>`__ integration.

Hyperlend is a next-generation lending platform on Hyperliquid EVM chain. The platform
enables users to earn interest, borrow assets, and build applications on the Hyperliquid
ecosystem. Hyperlend integrates Aave's lending infrastructure into a high-performance
environment, offering real-time leverage, dynamic interest rates, and deep liquidity.

Wrapped HyperLiquidity Provider (WHLP) is a tokenised version of HyperLiquidity Provider (HLP).
By minting WHLP, users earn trading fees from Hyperliquid while retaining full liquidity and
DeFi composability on HyperEVM. The token appreciates over time as HLP yields accrue to it.
Users deposit USDT0 to mint WHLP, which represents a claim on the underlying HLP vault's earnings.

Key features:

- WHLP token appreciates over time as HLP yields accrue
- 10% performance fee on yield
- No management fees or deposit/withdrawal fees
- Vault shares can be used as collateral for borrowing in Isolated Pools
- Managed by Paxos Labs

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/hyperlend>`__
- `Homepage <https://hyperlend.finance/>`__
- `Documentation <https://docs.hyperlend.finance/>`__
- `wHLP documentation <https://docs.loopingcollective.org/products/wrapped-hlp>`__
- `GitHub <https://github.com/hyperlendx>`__
- `Twitter <https://x.com/hyperlendx>`__
- `DefiLlama <https://defillama.com/protocol/hyperlend>`__
- `Contract on HyperEVM <https://hyperevmscan.io/address/0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5>`__


.. autosummary::
   :toctree: _autosummary_hyperlend
   :recursive:

   eth_defi.erc_4626.vault_protocol.hyperlend.vault
