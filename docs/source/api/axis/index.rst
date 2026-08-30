Axis API
========

Axis StakedUSDx support is exposed through the shared ERC-4626 classification
API. The reviewed Ethereum V2 and Plasma V1 contracts are classified by their
chain-aware hardcoded addresses and read through
:py:class:`eth_defi.erc_4626.vault_protocol.axis.vault.AxisVault`.

See :doc:`the Axis vault documentation </vaults/axis/index>` for product and
contract references.

.. autosummary::
   :toctree: _autosummary_axis
   :recursive:

   eth_defi.erc_4626.vault_protocol.axis.constants
   eth_defi.erc_4626.vault_protocol.axis.tags
   eth_defi.erc_4626.vault_protocol.axis.vault
