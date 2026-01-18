infiniFi API
------------

`infiniFi <https://infinifi.xyz/>`__ protocol integration.

infiniFi is a DeFi protocol that recreates fractional reserve banking on-chain.
The protocol enables users to deposit stablecoins to mint iUSD receipt tokens,
which can then be staked for yield through siUSD (liquid staking) or liUSD (locked staking).

By merging liquid and illiquid assets into a capital-efficient system with transparent
fractional reserves, infiniFi delivers superior yields to depositors. The protocol
allocates capital into yield strategies through integrations with Aave, Pendle,
Fluid, and Ethena, while maintaining reserves for redemptions.

In case of losses, the protocol has an explicit waterfall mechanism: locked liUSD
holders absorb losses first, then siUSD stakers, and finally plain iUSD holders.

Links
~~~~~

- `Homepage <https://infinifi.xyz/>`__
- `App <https://app.infinifi.xyz/deposit>`__
- `Twitter <https://x.com/infinifi_>`__
- `GitHub <https://github.com/InfiniFi-Labs/infinifi-protocol>`__
- `Documentation <https://research.nansen.ai/articles/understanding-infini-fi-the-on-chain-fractional-reserve-banking-protocol>`__
- `DefiLlama <https://defillama.com/protocol/infinifi>`__
- `Audit (Cantina) <https://cantina.xyz/competitions/2ac7f906-1661-47eb-bfd6-519f5db0d36b>`__

.. autosummary::
   :toctree: _autosummary_infinifi
   :recursive:

   eth_defi.erc_4626.vault_protocol.infinifi.vault
