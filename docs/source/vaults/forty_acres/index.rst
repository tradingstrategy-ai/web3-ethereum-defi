40acres API
-----------

`40acres <https://www.40acres.finance/>`__ integration.

40acres is a cashflow lending protocol for revenue-generating on-chain assets, primarily vote-escrowed NFTs (veNFTs) from DEXes like Aerodrome, Velodrome, Pharaoh, and Blackhole. Users deposit their veNFTs as collateral and receive instant USDC loans calculated against the assets' expected future rewards. Loans are self-repaying: each week, the veNFT's voting rewards are automatically collected and applied toward the loan balance, with no recurring interest and no risk of liquidation.

On the lending side, 40acres operates a peer-to-pool model with ERC-4626-compliant USDC supply vaults. Anyone can deposit USDC to earn organic yield sourced from real DEX trading fees and bribes (not token emissions). The protocol is live on Base, Optimism, and Avalanche.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/40acres>`__
- `Homepage <https://www.40acres.finance/>`__
- `Documentation <https://docs.40acres.finance/>`__
- `Twitter <https://x.com/40acres_finance>`__
- `DefiLlama <https://defillama.com/protocol/40-acres>`__
- `GitHub <https://github.com/40-Acres/loan-contracts>`__
- `Audits (4 independent audits by Sherlock) <https://docs.40acres.finance/security>`__
- `Bug bounty (up to $50,000) <https://audits.sherlock.xyz/bug-bounties/102>`__

.. autosummary::
   :toctree: _autosummary_forty_acres
   :recursive:

   eth_defi.erc_4626.vault_protocol.forty_acres.vault
