Secured Finance API
-------------------

`Secured Finance <https://secured.finance/>`__ integration.

Secured Finance is a fixed-rate lending protocol whose vault products package lender positions into ERC-4626 vaults. This repository currently identifies the Ethereum JPYC lender vault by hardcoded address and exposes it through a dedicated protocol wrapper.

Fee support is not yet mapped for this protocol, so management and performance fee reads currently return unknown values.

Links
~~~~~

- `Homepage <https://secured.finance/>`__
- `App <https://vaults.secured.finance/>`__
- `Documentation <https://docs.secured.finance/>`__
- `GitHub <https://github.com/secured-finance>`__
- `X <https://x.com/Secured_Fi>`__
- `DefiLlama <https://defillama.com/protocol/secured-finance>`__
- `Contract on Etherscan <https://etherscan.io/address/0x6f6046e59501e484152d46045ba5eecf1cab8935>`__

.. autosummary::
   :toctree: _autosummary_secured_finance
   :recursive:

   eth_defi.erc_4626.vault_protocol.secured_finance.vault
