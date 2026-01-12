Singularity Finance API
-----------------------

`Singularity Finance <https://singularityfinance.ai/>`__ integration.

Singularity Finance (SFI) is the on-chain infrastructure powering the AI economy.
The platform provides the foundational layer for AI-driven finance applications to launch,
optimise, and scale operations on blockchain networks.

The DynaVaults framework implements the ERC4626 vault standard with EIP-5143 slippage
protection to prevent value extraction during vault operations. The vaults use AI-driven
strategies to optimise yield across DeFi protocols.

Key features:

- **ERC4626 compliant**: Standard vault interface for deposits and redemptions
- **EIP-5143 slippage protection**: Transactions revert when slippage exceeds thresholds
- **EIP-1167 minimal proxy pattern**: Gas-efficient contract deployment
- **Role-based access control**: OpenZeppelin AccessControl with custom IAM

Links
~~~~~

- `Homepage <https://singularityfinance.ai/>`__
- `Documentation <https://docs.singularityfinance.ai/>`__
- `Twitter <https://x.com/singularity_fi>`__
- `DefiLlama <https://defillama.com/protocol/singularity-finance>`__
- `Example vault (Base) <https://basescan.org/address/0xdf71487381Ab5bD5a6B17eAa61FE2E6045A0e805>`__

.. autosummary::
   :toctree: _autosummary_singularity
   :recursive:

   eth_defi.erc_4626.vault_protocol.singularity.vault
