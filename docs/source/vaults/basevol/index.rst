BaseVol API
-----------

`BaseVol <https://basevol.com/>`__ integration.

BaseVol is an onchain options protocol on Base, offering zero-day-to-expiry (0DTE)
binary options trading and AI-managed yield vaults. 

The Genesis Vault systematically deploys funds using a 90/10 allocation: 90% to
USDC lending on Morpho/Spark, and 10% to 0DTE options selling. The vault is managed
by A.T.M. (Autonomous Trading Machine), an AI agent that handles trade sizing,
hedging, and settlement. 

The vaults use Diamond proxy (EIP-2535) architecture.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/basevol>`__
- `Homepage <https://basevol.com/>`__
- `Documentation <https://basevol.gitbook.io/docs/>`__
- `Twitter <https://x.com/BaseVolApp>`__
- `Audit (FailSafe) <https://getfailsafe.com/basevol-smart-contract-audit/>`__
- `DefiLlama <https://defillama.com/protocol/basevol>`__
- `Genesis Vault on Basescan <https://basescan.org/address/0xf1BE2622fd0f34d520Ab31019A4ad054a2c4B1e0>`__

.. autosummary::
   :toctree: _autosummary_basevol
   :recursive:

   eth_defi.erc_4626.vault_protocol.basevol.vault
