Spectra API
-----------

`Spectra Finance <https://www.spectra.finance/>`__ integration.

Spectra is an open-source interest rate derivatives protocol that enables yield tokenisation.
The protocol splits ERC-4626 compliant interest-bearing tokens into Principal Tokens (PT) and
Yield Tokens (YT), allowing users to fix rates, trade yield, and earn on liquidity positions.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/spectra>`__
- `Homepage <https://www.spectra.finance/>`__
- `App <https://app.spectra.finance>`__
- `Documentation <https://docs.spectra.finance/>`__
- `Twitter <https://x.com/spectra_finance>`__
- `GitHub <https://github.com/perspectivefi>`__

About Spectra ERC4626 wrappers
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The integration supports Spectra ERC-4626 wrapper contracts that wrap various rebasing tokens
to make them compatible with Spectra's yield tokenisation system.

**Note:** These wrappers are NOT core Spectra yield tokenisation vaults. They are ERC-4626
wrappers that enable rebasing tokens to be used within Spectra's PT/YT system.

Currently supported wrappers:

- **sw-WUSDN** (Ethereum) - Wrapper for WUSDN (SmarDex delta-neutral synthetic dollar)
- **sw-earn** (Monad) - Generic ERC4626 wrapper for rebasing tokens

About WUSDN
~~~~~~~~~~~

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

   eth_defi.erc_4626.vault_protocol.spectra.erc4626_wrapper_vault
