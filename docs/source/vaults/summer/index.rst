Summer.fi API
-------------

`Summer.fi <https://summer.fi/>`__ integration.

Summer.fi is a unified frontend offering two distinct yet complementary products:

- **Lazy Summer Protocol**: A hands-free, passive lending protocol for effortless yield optimisation
- **Summer.fi Pro**: An advanced lending, borrowing, and earning app for more experienced DeFi users

Summer.fi is an app designed to simplify and enhance users' interactions with lending, borrowing,
and yield generation. The protocol provides access to DeFi yields continually rebalanced by AI
powered Keepers.

Security note
~~~~~~~~~~~~~

On 2026-07-06, `CryptoBriefing reported <https://cryptobriefing.com/blockaid-detects-6m-exploit-summer-fi/>`__
that Blockaid flagged an active Summer.fi exploit on Ethereum, with approximately USD 6 million
in DAI drained from three contracts including ``0x98C49e13bf99D7CAd8069faa2A370933EC9EcF17``.
Until the incident is fully resolved and an official post-mortem is available, Summer.fi vaults
are treated as illiquid and blacklisted in the vault risk metadata.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/summer-fi>`__
- `Homepage <https://summer.fi/>`__
- `Documentation <https://docs.summer.fi/>`__
- `GitHub <https://github.com/oasisdex>`__
- `Twitter <https://x.com/summerfinance_>`__
- `DefiLlama <https://defillama.com/protocol/summer.fi>`__

.. autosummary::
   :toctree: _autosummary_summer
   :recursive:

   eth_defi.erc_4626.vault_protocol.summer.vault
