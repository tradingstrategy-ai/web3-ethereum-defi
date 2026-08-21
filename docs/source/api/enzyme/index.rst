Enzyme protocol API
-------------------

Enzyme has two separate vault architectures. The established Enzyme Blue
helpers use a paired VaultProxy and ComptrollerProxy, while the Onyx adapter
uses a standalone Shares contract and component system. Their feature flags
are respectively ``enzyme_blue_like`` and ``enzyme_onyx_like``.

The read-only Onyx adapter discovers official Base factory deployments and
reads metadata, stored share prices and total value. The direct Blue adapter
discovers reviewed Dispatcher deployments on Ethereum, Polygon, Base and Arbitrum,
then reads its paired ComptrollerProxy for current and historical GAV data.

.. autosummary::
   :toctree: _autosummary_enzyme
   :recursive:

   eth_defi.enzyme.deployment
   eth_defi.enzyme.vault
   eth_defi.enzyme.blue_discovery
   eth_defi.enzyme.fee
   eth_defi.enzyme.blue_vault
   eth_defi.enzyme.blue_historical
   eth_defi.enzyme.onyx_discovery
   eth_defi.enzyme.onyx_permission
   eth_defi.enzyme.onyx_vault
   eth_defi.enzyme.onyx_historical
   eth_defi.enzyme.offchain_metadata
   eth_defi.enzyme.integration_manager
   eth_defi.enzyme.events
   eth_defi.enzyme.price_feed
   eth_defi.enzyme.generic_adapter
   eth_defi.enzyme.generic_adapter_vault
   eth_defi.enzyme.utils
   eth_defi.enzyme.erc20
   eth_defi.enzyme.uniswap_v2
   eth_defi.enzyme.policy
   eth_defi.enzyme.vault_controlled_wallet
   eth_defi.enzyme.uniswap_v3
