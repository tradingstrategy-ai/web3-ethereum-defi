Spectra API
-----------

`Spectra Finance <https://www.spectra.finance/>`__ integration.

Spectra is an open-source interest rate derivatives protocol that enables yield tokenisation.
The protocol splits ERC-4626 compliant interest-bearing tokens into Principal Tokens (PT) and
Yield Tokens (YT), allowing users to fix rates, trade yield, and earn on liquidity positions.

Spectra Finance:

- `Homepage <https://www.spectra.finance/>`__
- `App <https://app.spectra.finance>`__
- `Documentation <https://docs.spectra.finance/>`__
- `Twitter <https://x.com/spectra_finance>`__
- `GitHub <https://github.com/perspectivefi>`__

About WUSDN
~~~~~~~~~~~

The current integration supports the Spectra ERC-4626 wrapper for WUSDN (Wrapped Ultimate
Synthetic Delta Neutral), which is a wrapper contract that makes WUSDN compatible with
Spectra's yield tokenisation system.

**Note:** This wrapper is NOT a core Spectra yield tokenisation vault. It is an ERC-4626
wrapper that enables WUSDN to be used within Spectra's PT/YT system.

WUSDN is a non-rebasing wrapper around USDN, a decentralised synthetic US dollar from
`SmarDex <https://smardex.io/usdn>`__ that uses a delta-neutral strategy to generate yield.

USDN / SmarDex (underlying protocol):

- `Homepage <https://smardex.io/>`__
- `USDN app <https://smardex.io/usdn>`__
- `Documentation <https://docs.smardex.io/ultimate-synthetic-delta-neutral>`__
- `Twitter <https://x.com/SmarDex>`__

.. autosummary::
   :toctree: _autosummary_spectra
   :recursive:

   eth_defi.erc_4626.vault_protocol.spectra.wusdn_vault
