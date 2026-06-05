CrystalClear API
----------------

`CrystalClear <https://crystalclear.finance/>`__ integration.

CrystalClear builds algorithmic trading vaults on `Hyperliquid <https://hyperliquid.xyz/>`__'s
HyperEVM that trade perpetual futures on HyperCore. Each vault runs a distinct quantitative
strategy — diversified, momentum-tilted, high-conviction, or low-turnover — with automated
two-week rebalancing cycles where the live strategy roster is replaced with a freshly evolved
cohort, out-of-sample validated before going live.

The vaults use ERC-4626 with a UUPS proxy pattern (OpenZeppelin v5), USDC denomination,
and a two-step withdrawal mechanism (``requestWithdraw()`` then ``claimWithdraw()``).
A 20% performance fee is charged at redemption (externalised).

Links
~~~~~

- `Homepage <https://crystalclear.finance/>`__
- `App <https://app.crystalclear.finance/app.html#vaults>`__
- `Docs <https://crystalclear.gitbook.io/crystalclear-docs/>`__
- `Twitter <https://x.com/CrystalClearHL>`__

.. autosummary::
   :toctree: _autosummary_crystalclear
   :recursive:

   eth_defi.erc_4626.vault_protocol.crystalclear.vault
