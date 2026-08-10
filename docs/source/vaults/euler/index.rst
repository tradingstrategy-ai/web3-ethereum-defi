Euler API
----------

`Euler Finance <https://www.euler.finance/>`__ integration.

Euler is a modular lending platform that allows users to lend and borrow ERC-20 crypto assets
in a decentralised, non-custodial manner. The platform is built on Ethereum and enables any
asset to become collateral for a lending market.

Euler v2 has two main components:

- **Euler Vault Kit (EVK)**: A modular framework for building credit vaults - permissionless,
  composable ERC-4626 lending pools with added borrowing functionality
- **Ethereum Vault Connector (EVC)**: An interoperability layer that connects vaults and allows
  their use as collateral for other vaults

Key features:

- Permissionless vault deployment with customisable risk parameters
- Multicall batching and flash liquidity via EVC
- Market-leading risk-adjusted rates for lenders and borrowers
- Institutional-grade security with multiple audits

Deposit permissions
~~~~~~~~~~~~~~~~~~~

Euler Earn deposits are permissionless. EVK vaults expose a hook target and
operation bitmap through ``hookConfig()``. When neither the deposit nor mint
operation is hooked, deposits are permissionless. A non-zero custom hook can
implement arbitrary policy, so the adapter leaves it unknown until that hook
implementation is recognised. A zero hook target with operation bits set is a
global operation disable, not an account whitelist. See the `EVK hook design
<https://github.com/euler-xyz/euler-vault-kit/blob/master/docs/whitepaper.md#hooks>`__.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/euler>`__
- `Homepage <https://www.euler.finance/>`__
- `App <https://app.euler.finance/>`__
- `Documentation <https://docs.euler.finance/>`__
- `GitHub <https://github.com/euler-xyz>`__
- `Twitter <https://x.com/eulerfinance>`__
- `DefiLlama <https://defillama.com/protocol/euler>`__

.. autosummary::
   :toctree: _autosummary_euler
   :recursive:

   eth_defi.erc_4626.vault_protocol.euler.vault
   eth_defi.erc_4626.vault_protocol.euler.offchain_metadata
