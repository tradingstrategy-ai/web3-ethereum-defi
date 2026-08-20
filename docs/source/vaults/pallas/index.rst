Pallas
------

`Pallas <https://app.pallas.fund/>`__ operates USDT0-denominated trading vaults
on HyperEVM and HyperCore. The active Basis Trading HIP-3 and Directional
Volatility strategies issue PALLAS shares and use an asynchronous
request-and-claim flow for deposits and redemptions.

The reviewed deployments are ERC-1967 upgradeable proxies over a verified
``ERC7540NonCustodialTradingVaultUpgradeable`` implementation. The library
recognises the two confirmed HyperEVM addresses through a chain-aware hardcoded
registry and deliberately does not advertise a synchronous deposit manager.

Links
~~~~~

- `Pallas app <https://app.pallas.fund/>`__
- `Basis Trading HIP-3 vault <https://hyperevmscan.io/address/0x9b3aa83BD833123437d4efa656E7121B7F317899>`__
- `Directional Volatility vault <https://hyperevmscan.io/address/0xa642188e1345AEe1809f6db5431464b079978c68>`__
- `Twitter <https://x.com/pallas_vault>`__

.. autosummary::
   :toctree: _autosummary_pallas
   :recursive:

   eth_defi.erc_4626.vault_protocol.pallas.vault
