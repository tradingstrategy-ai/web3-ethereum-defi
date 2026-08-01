"""Published Shift vault deployment registry."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(frozen=True, slots=True)
class ShiftVaultProduct:
    """One published ShiftVault deployment.

    Shift publishes its current vault addresses in its public address registry.
    The contracts are custom ERC-20 share vaults, so the registry is deliberately
    chain-aware instead of relying on an ERC-4626 interface probe.

    :param chain_id:
        EVM chain identifier hosting the vault.
    :param vault_address:
        ShiftVault ERC-20 share-token contract address.
    :param product_name:
        Public Shift product name.
    :param first_seen_at_block:
        First EVM block with vault bytecode.
    :param first_seen_at:
        Naive UTC timestamp of ``first_seen_at_block``.
    """

    chain_id: int
    vault_address: HexAddress
    product_name: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


#: Shift's public app and published address registry.
SHIFT_HOMEPAGE = "https://app.shiftprotocol.xyz/"

#: Public address registry maintained by Shift.
#:
#: https://shiftprotocol.gitbook.io/shift/resources/addresses
SHIFT_VAULT_PRODUCTS: dict[tuple[int, HexAddress], ShiftVaultProduct] = {
    (8453, "0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e"): ShiftVaultProduct(
        chain_id=8453,
        vault_address="0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e",
        product_name="Shift ltPARA",
        # First Base block with deployed proxy bytecode, found using an
        # archive-RPC binary search on 2026-07-27.
        first_seen_at_block=35_705_986,
        first_seen_at=datetime.datetime(2025, 9, 18, 13, 15, 19, tzinfo=datetime.UTC).replace(tzinfo=None),
    ),
    (8453, "0x4ce3ec1b7b4ffb33a0b70c64a0560a3f341aa2e1"): ShiftVaultProduct(
        chain_id=8453,
        vault_address="0x4ce3ec1b7b4ffb33a0b70c64a0560a3f341aa2e1",
        product_name="Shift extUSD",
        # First Base block with deployed proxy bytecode, found using an
        # archive-RPC binary search on 2026-07-27.
        first_seen_at_block=38_936_930,
        first_seen_at=datetime.datetime(2025, 12, 2, 8, 13, 27, tzinfo=datetime.UTC).replace(tzinfo=None),
    ),
    (42161, "0x956bdd9c18b786b082fd50c52722d254f0cb6964"): ShiftVaultProduct(
        chain_id=42161,
        vault_address="0x956bdd9c18b786b082fd50c52722d254f0cb6964",
        product_name="Shift ltLLP",
        # First Arbitrum block with deployed proxy bytecode, found using an
        # archive-RPC binary search on 2026-07-27.
        first_seen_at_block=434_181_142,
        first_seen_at=datetime.datetime(2026, 2, 20, 19, 0, 51, tzinfo=datetime.UTC).replace(tzinfo=None),
    ),
}


#: Reviewed ShiftVault addresses grouped by EVM chain for classification.
#:
#: Derived from :data:`SHIFT_VAULT_PRODUCTS` to keep the reviewed deployment
#: registry as the sole address source of truth.
SHIFT_VAULTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {chain_id: frozenset(product.vault_address for product in SHIFT_VAULT_PRODUCTS.values() if product.chain_id == chain_id) for chain_id in {product.chain_id for product in SHIFT_VAULT_PRODUCTS.values()}}

#: All reviewed ShiftVault addresses, for address-only compatibility callers.
SHIFT_VAULT_ADDRESSES = frozenset(product.vault_address for product in SHIFT_VAULT_PRODUCTS.values())

#: Hardcoded discovery leads for Shift's custom, non-ERC-4626 share vaults.
SHIFT_HARDCODED_LEADS = tuple((product.chain_id, product.vault_address, product.first_seen_at_block, product.first_seen_at) for product in SHIFT_VAULT_PRODUCTS.values())
