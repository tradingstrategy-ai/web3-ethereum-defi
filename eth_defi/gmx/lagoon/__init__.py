"""GMX trading integration for Lagoon vaults.

This module provides a `LagoonGMXTradingWallet` class that implements the `BaseWallet`
interface, enabling the standard GMX CCXT adapter to trade through a Lagoon
vault without any modifications.

Example usage::

    from eth_defi.gmx.ccxt import GMX
    from eth_defi.gmx.lagoon import LagoonGMXTradingWallet
    from eth_defi.erc_4626.vault_protocol.lagoon import LagoonVault
    from eth_defi.hotwallet import HotWallet

    # Set up vault and asset manager
    vault = LagoonVault(web3, vault_address)
    asset_manager = HotWallet.from_private_key("0x...")

    # Create vault wallet
    wallet = LagoonGMXTradingWallet(vault, asset_manager)

    # Use standard GMX adapter with vault wallet
    gmx = GMX(params={"rpcUrl": rpc_url}, wallet=wallet)
    gmx.load_markets()

    # Trade through vault - standard GMX API
    order = gmx.create_order(
        symbol="ETH/USD",
        type="market",
        side="buy",
        amount=0,
        params={"size_usd": 1000, "leverage": 2.0},
    )

"""

from eth_defi.gmx.lagoon.wallet import LagoonGMXTradingWallet
from eth_defi.gmx.lagoon.approvals import (
    approve_gmx_collateral_via_vault,
    approve_gmx_execution_fee_via_vault,
)

__all__ = [
    "LagoonGMXTradingWallet",
    "approve_gmx_collateral_via_vault",
    "approve_gmx_execution_fee_via_vault",
]
