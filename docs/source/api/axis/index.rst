Axis API
========

Axis StakedUSDx support is exposed through the shared ERC-4626 classification
API. The reviewed Ethereum V2 and Plasma V1 contracts are classified by their
chain-aware hardcoded addresses and read through
:py:class:`eth_defi.erc_4626.vault_protocol.axis.vault.AxisVault`.

Ethereum V2 is marked as ERC-7540 and ERC-7575. Plasma V1 is deliberately not:
it exposes an older configurable cooldown interface. Both deployments retain
the standard ERC-4626 accounting reads used by the current and historical state
readers. Current-state and fixed-block reader regressions read V2 through the
shared ABI. The current-state test checks interface compatibility without
pinning mutable implementation or cooldown values, while the fixed-block
regression retains exact historical assertions.

See :doc:`the Axis vault documentation </vaults/axis/index>` for product and
contract references.

.. autosummary::
   :toctree: _autosummary_axis
   :recursive:

   eth_defi.erc_4626.vault_protocol.axis.constants
   eth_defi.erc_4626.vault_protocol.axis.tags
   eth_defi.erc_4626.vault_protocol.axis.vault
