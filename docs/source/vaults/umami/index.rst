Umami API
----------

`Umami DAO <https://umami.finance/>`__ vault protocol integration.

Built on the upgraded GMX v2 platform, GM Vaults enable users to deposit single tokens like BTC,
ETH, USDC and ARB, while eliminating impermanent loss through automated hedging called "internal
netting". These vaults utilise the GM Index (GMI) to manage deposits and generate yield from four
distinct synthetic pools on GMX V2, ensuring a stable and lucrative environment for depositors.

A crucial element of Umami's hedging strategy is internal netting. Each of its Vaults acts as a
hedging counterparty to the others. Instead of costly external hedges, Umami's Vaults swap delta
among themselves while keeping the vast majority of their TVL deployed to generate yield.

Links
~~~~~

- `Homepage <https://umami.finance/>`__
- `App <https://umami.finance/vaults>`__
- `Documentation <https://about.umami.finance/>`__
- `Twitter <https://x.com/umamifinance>`__
- `DefiLlama <https://defillama.com/protocol/umami-finance>`__

.. autosummary::
   :toctree: _autosummary_euler
   :recursive:

   eth_defi.erc_4626.vault_protocol.umami.vault
