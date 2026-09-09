Yearn vaults API
----------------

`Yearn Finance <https://yearn.fi/>`__ vault integration.

Yearn Vaults (yVaults) are capital pools that automatically generate yield based on opportunities
present in the market. Vaults benefit users by socialising gas costs, automating the yield generation
and rebalancing process, and automatically shifting capital as opportunities arise. End users do not
need extensive knowledge of the underlying DeFi protocols and can use the vaults as passive-investing
strategies.

With yVaults v3, vaults can be made from a single strategy or a collection of multiple strategies
which balance funds between them. Users have more control over where they want their funds to go
and a wider range of risk appetites.

The deposit manager detects a global shutdown or full vault-wide deposit limit
before creating a transaction. When the selected account has already approved
the requested assets, it also simulates that exact deposit and returns a typed
admission rejection for a confirmed EVM revert. The result includes raw revert
data and its selector when the RPC provider supplies them, but does not label an
opaque provider message as a decoded Solidity error. It does not treat a
missing approval or an RPC failure as a vault closure.

Public catalogue metadata
~~~~~~~~~~~~~~~~~~~~~~~~~

The scanner caches the public `yDaemon detected-vault catalogue
<https://ydaemon.yearn.fi/vaults/detected?limit=2000>`__ daily. A matching
address positively confirms that Yearn publishes a vault page and supplies the
exported description; its bounded first sentence is the short description.
Template descriptions with unresolved placeholders are omitted rather than
shown on the website.

The catalogue is not a complete registry for TokenizedStrategy, compounder, or
Morpho compounder adapter shapes. Their absence is therefore always unknown,
never ``unofficial``. For direct Yearn V3 vaults only, the versioned `yDaemon
source metadata <https://github.com/yearn/ydaemon/tree/main/data/meta/vaults>`__
can identify an explicit static non-endorsement. A public-page match overrides
that static result, covering freshly launched vaults before the source files
catch up. These are website membership signals, not safety ratings or
investment recommendations.

Use ``scripts/erc-4626/migrate-yearn-vault-metadata.py`` to repair existing
cached Yearn rows. It defaults to a non-mutating dry run and changes only the
metadata pickle after a successful catalogue download.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/yearn>`__
- `Homepage <https://yearn.fi/>`__
- `App <https://yearn.fi/vaults>`__
- `Documentation <https://docs.yearn.fi/>`__
- `GitHub <https://github.com/yearn>`__
- `Twitter <https://x.com/yearnfi>`__
- `DefiLlama <https://defillama.com/protocol/yearn-finance>`__

.. autosummary::
   :toctree: _autosummary_yearn
   :recursive:

   eth_defi.erc_4626.vault_protocol.yearn.deposit_redeem
   eth_defi.erc_4626.vault_protocol.yearn.vault
   eth_defi.erc_4626.vault_protocol.yearn.compounder
   eth_defi.erc_4626.vault_protocol.yearn.morpho_compounder
   eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata
