Gauntlet API
------------

`Gauntlet <https://www.gauntlet.xyz/>`__ integration.

Gauntlet is a DeFi-native quantitative research firm specialising in risk management,
incentive optimisation, and vault curation. Gauntlet curates 80+ vaults across multiple
protocols, managing over $1B in TVL.

Gauntlet uses `Aera <https://docs.aera.finance/>`__ as its onchain vault infrastructure.
Two contract types are used: VaultV2 (adapter-based architecture with allocate/deallocate)
and MultiDepositorVault (guardian-based architecture with provisioner pattern).

- `DefiLlama <https://defillama.com/protocol/gauntlet>`__
- `Audits <https://www.gauntlet.xyz/vaults/security>`__

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/gauntlet>`__
- `Homepage <https://www.gauntlet.xyz/>`__
- `Documentation <https://vaultbook.gauntlet.xyz/>`__
- `Aera docs <https://docs.aera.finance/>`__
- `GitHub <https://github.com/GauntletNetworks/aera-contracts-public>`__
- `Twitter <https://x.com/gauntlet_xyz>`__

.. autosummary::
   :toctree: _autosummary_gauntlet
   :recursive:

   eth_defi.erc_4626.vault_protocol.gauntlet.vault
