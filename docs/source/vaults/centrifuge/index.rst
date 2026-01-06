Centrifuge API
--------------

`Centrifuge <https://centrifuge.io/>`__ vault integration.

Centrifuge is a protocol for real-world asset (RWA) tokenisation and financing.
It enables borrowers to finance assets like invoices, real estate, and other
tokenised assets without the need for banks or other intermediaries.

Each pool can have multiple tranches, and each tranche is a separate deployment
of an ERC-7540 Vault and a Tranche Token. Additionally, each tranche of a
Centrifuge pool can have multiple Liquidity Pools (vaults) - one for each
supported investment currency.

The protocol implements ERC-7540 (asynchronous deposits/redemptions) on top of
ERC-4626, enabling integration with Centrifuge's epoch-based investment system.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/centrifuge>`__
- `Homepage <https://centrifuge.io/>`__
- `App <https://app.centrifuge.io/>`__
- `Documentation <https://docs.centrifuge.io/>`__
- `Developer docs <https://developer.centrifuge.io/>`__
- `GitHub <https://github.com/centrifuge/liquidity-pools>`__
- `Twitter <https://x.com/centrifuge>`__
- `DefiLlama <https://defillama.com/protocol/centrifuge>`__

One of the most popular Centrifuge users is `Anemoy <https://www.anemoy.io/>`__,
which offers tokenised US assets like S&P 500 index, Janus Henderson AAA collaterlised loan obligation ETF and US treasuries.

.. autosummary::
   :toctree: _autosummary_centrifuge
   :recursive:

   eth_defi.erc_4626.vault_protocol.centrifuge.vault
   eth_defi.erc_4626.vault_protocol.centrifuge.centrifuge_utils
