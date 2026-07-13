KiloEx
------

`KiloEx <https://www.kiloex.io/>`__ is a multi-chain perpetual DEX. Its
`Hybrid Vault <https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault>`__
provides the counterparty liquidity for trader positions. The ERC-4626 vault
shares include kUSDT and kUSDC, while the underlying VUSD accounting asset
represents quote-asset collateral.

The vault return depends on the platform's trading activity, including a share
of fee revenue, funding payments and trader profit and loss. Withdrawal requests
settle in three-day epochs; KiloEx documents waits of one to three epochs based
on the vault collateral ratio.

The integration uses a chain-aware hardcoded address matrix because KiloEx
Hybrid Vault contracts expose the same `maxDiscountP()` selector as Gains
Network gToken vaults. This prevents the contracts being incorrectly classified
as gTrade.

Links
~~~~~

- `KiloEx Earn <https://app.kiloex.io/earn/>`__
- `Documentation <https://docs.kiloex.io/>`__
- `Fees and spread <https://docs.kiloex.io/kiloex/trading/fees-and-spread>`__
- `Audits <https://docs.kiloex.io/kiloex/about-kiloex/audit>`__
- `GitHub <https://github.com/KiloExPerp>`__
- `Twitter <https://x.com/KiloEx_perp>`__
- `DefiLlama <https://defillama.com/protocol/kiloex>`__

.. autosummary::
   :toctree: _autosummary_kiloex
   :recursive:

   eth_defi.erc_4626.vault_protocol.kiloex.vault
