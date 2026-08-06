D2 Finance API
-------------------

`D2 Finance <https://d2.finance/>`__ vault integration.

D2 Finance delivers real yield at scale — combining DeFi-native alpha with TradFi expertise to
provide institutional-grade stablecoin, RWA and BTC-backed structured strategies. Powered by
Hyperliquid and other EVM chains.

The protocol offers high-yield strategies on stablecoins, Bitcoin, and real-world assets,
designed for both retail and institutional investors seeking managed yield products.

D2 withdrawals are epoch-based rather than cooldown-based. Public lifetime
metrics therefore export ``withdrawal_delay_type: "epoch"``, with a zero
minimum wait when the withdrawal phase is open and the current epoch duration
as the maximum normal wait.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/d2-finance>`__
- `Homepage <https://d2.finance/>`__
- `Twitter <https://x.com/D2_Finance>`__
- `DefiLlama <https://defillama.com/protocol/d2-finance>`__

.. autosummary::
   :toctree: _autosummary_d2
   :recursive:

   eth_defi.erc_4626.vault_protocol.d2.vault
