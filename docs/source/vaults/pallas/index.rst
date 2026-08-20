Pallas
------

`Pallas <https://app.pallas.fund/>`__ operates USDT0-denominated vault contracts
on HyperEVM. The reviewed Basis Trading HIP-3 and Directional Volatility
strategies trade Hyperliquid HIP-3 perpetual markets and issue PALLAS shares.
The application is a private beta with whitelisted access.

The reviewed deployments are ERC-1967 upgradeable proxies over verified
``ERC7540NonCustodialTradingVaultUpgradeable`` implementations. Their public
ABI uses custom one-argument request and claim methods rather than the standard
ERC-7540 signatures. The integration therefore supports read-only vault data
and does not advertise a deposit manager.

Fees
~~~~

The contracts expose annual management and profit-based performance fees in
basis points. Both are accrued by minting shares to the fee recipient. The
Pallas application separately advertises Premium Pass rebates, which are not
the configured contract fee rates.

Links
~~~~~

- `Basis Trading HIP-3 vault <https://hyperevmscan.io/address/0x9b3aa83BD833123437d4efa656E7121B7F317899>`__
- `Directional Volatility vault <https://hyperevmscan.io/address/0xa642188e1345AEe1809f6db5431464b079978c68>`__
- `Basis Trading implementation <https://hyperevmscan.io/address/0xe324e4a5C9f8ea9Db2F957702d4Bb164DE3caF17#code>`__
- `Directional Volatility implementation <https://hyperevmscan.io/address/0x14FBcFD4279e326ADa2A0c682e77E9D686Bc310B#code>`__
- `Twitter <https://x.com/pallas_vault>`__

.. autosummary::
   :toctree: _autosummary_pallas
   :recursive:

   eth_defi.erc_4626.vault_protocol.pallas.constants
   eth_defi.erc_4626.vault_protocol.pallas.vault
