ForgeYields API
---------------

`ForgeYields <https://www.forgeyields.com/>`__ is a cross-chain, non-custodial yield
aggregator deploying into frontier DeFi strategies underwritten by the
`Hallmark <https://forge-labs.gitbook.io/forge-docs/hallmark/overview>`__ public risk
methodology.

The fyUSDC, fyETH and fyWBTC vaults issue auto-compounding ERC-4626 tokens (fyTokens).
The Ethereum vault is built on Veda Labs' BoringVault and allocates across Aave, Morpho,
Curve, Pendle and others.

Links
~~~~~

- `Homepage <https://www.forgeyields.com/>`__
- `App <https://app.forgeyields.com/>`__
- `Documentation <https://forge-labs.gitbook.io/forge-docs>`__
- `Twitter <https://x.com/ForgeYields>`__
- `GitHub <https://github.com/ForgeYields>`__
- `Audits <https://forge-labs.gitbook.io/forge-docs/other/audits>`__
- `DefiLlama <https://defillama.com/protocol/forgeyields>`__

.. autosummary::
   :toctree: _autosummary_forgeyields
   :recursive:

   eth_defi.erc_4626.vault_protocol.forgeyields.vault
