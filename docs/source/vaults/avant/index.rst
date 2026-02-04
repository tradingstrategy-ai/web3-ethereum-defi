Avant API
---------

`Avant Protocol <https://www.avantprotocol.com/>`__ integration.

Avant Protocol is a decentralised finance platform on Avalanche that offers on-chain yield-bearing
assets through an adaptive multi-manager architecture. The protocol deploys capital across
delta-neutral strategies managed by specialised strategy teams.

The protocol offers avUSD, a stablecoin that can be staked to receive savUSD (Staked avUSD).
savUSD is an ERC-4626 vault token that earns yield from the protocol's reward distribution
mechanism with an 8-hour vesting period for distributed rewards.

Key features include multi-layer security with reserve funds and junior tranches for loss
protection, a transparent dashboard for capital allocation reporting, and risk-tiered
options through senior and junior tranches.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/avant>`__
- `Homepage <https://www.avantprotocol.com/>`__
- `Documentation <https://docs.avantprotocol.com/>`__
- `Twitter <https://x.com/avantprotocol>`__
- `GitHub <https://github.com/Avant-Protocol>`__
- `Audits <https://docs.avantprotocol.com/overview/audit-and-security>`__
- `DefiLlama <https://defillama.com/protocol/avant-protocol>`__

.. autosummary::
   :toctree: _autosummary_avant
   :recursive:

   eth_defi.erc_4626.vault_protocol.avant.vault
