USDai protocol API
-----------------

`USD.AI <https://usd.ai/>`__ integration.

USD.AI is a synthetic dollar protocol that bridges DeFi and artificial intelligence infrastructure.
The protocol issues USDai, an overcollateralised stablecoin pegged to $1 and redeemable for stablecoins
like USDC, with reserves deployed in income-generating AI/DePIN loans backed by NVIDIA GPUs.

The dual-token system includes:

- **USDai**: Non-yield-bearing synthetic dollar, highly liquid and composable
- **sUSDai**: Yield-bearing ERC-4626 vault shares received when staking USDai

Key features:

- Synthetic dollar backed by tokenised U.S. Treasuries through the M0 platform
- Yield generation from loans collateralised by GPU hardware in insured data centres
- Each GPU is documented under U.S. commercial law and tokenised as an NFT
- Chainlink Price Feeds integration for accurate onchain rates

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/usdai>`__
- `Homepage <https://usd.ai/>`__
- `App <https://usdai.fi/>`__
- `Documentation <https://usdai.gitbook.io/usdai/>`__
- `Twitter <https://x.com/USDai_Official>`__
- `DefiLlama <https://defillama.com/protocol/usd-ai>`__

.. autosummary::
   :toctree: _autosummary_usdai
   :recursive:

   eth_defi.erc_4626.vault_protocol.usdai.vault
