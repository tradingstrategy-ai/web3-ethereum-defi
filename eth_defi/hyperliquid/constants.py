"""Constants for the Hyperliquid integration.

Shared constants used across the Hyperliquid modules
(:py:mod:`~eth_defi.hyperliquid.daily_metrics`,
:py:mod:`~eth_defi.hyperliquid.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

from eth_typing import HexAddress

from eth_defi.vault.fee import VaultFeeMode

#: Synthetic in-house chain ID for Hypercore (Hyperliquid's native non-EVM layer).
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``9999: "Hypercore"``.
HYPERCORE_CHAIN_ID: int = 9999

#: Default path for Hyperliquid daily metrics DuckDB database.
HYPERLIQUID_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "hyperliquid-vaults.duckdb"

#: Fixed performance fee (profit share) for Hyperliquid native vault leaders.
#:
#: All Hyperliquid vaults use a fixed 10% profit share to the vault leader.
#: Protocol vaults (e.g. HLP) do not have any fees or profit share.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
HYPERLIQUID_VAULT_PERFORMANCE_FEE: float = 0.10

#: Fee mode for Hyperliquid native vaults.
#:
#: The leader's 10% profit share is deducted from depositor profits at withdrawal time.
#: The PnL history returned by the ``vaultDetails`` API already reflects the
#: depositor's net returns (after the leader's cut), so the share price we compute
#: from portfolio history is a net-of-fees price. This matches
#: :py:attr:`~eth_defi.vault.fee.VaultFeeMode.internalised_skimming` —
#: gross and net returns are identical from the pipeline's perspective.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders
HYPERLIQUID_VAULT_FEE_MODE: VaultFeeMode = VaultFeeMode.internalised_skimming

#: Lockup period for user-created Hyperliquid vaults.
#:
#: After depositing into a user vault, followers must wait 1 day
#: before they can withdraw.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors
HYPERLIQUID_USER_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(days=1)

#: Lockup period for Hyperliquid protocol vaults (HLP and sub-vaults).
#:
#: After depositing into the HLP or its child vaults, followers must
#: wait 4 days before they can withdraw.
#:
#: Source: https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors
HYPERLIQUID_PROTOCOL_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(days=4)


# ──────────────────────────────────────────────
# Well-known Hyperliquid system vault addresses
# ──────────────────────────────────────────────

#: HLP (Hyperliquidity Provider) parent vault address on mainnet.
#:
#: The HLP is the main protocol-operated market-making vault.
#: It has ``relationship_type="parent"`` in the Hyperliquid API,
#: with multiple child sub-vaults that handle different strategies.
#: No performance fee (0%), 4-day lockup period.
#: Leader: ``0x677d831aef5328190852e24f13c46cac05f984e7``
#:
#: Source: Hyperliquid ``vaultDetails`` API, https://app.hyperliquid.xyz/vaults
HLP_VAULT_ADDRESS_MAINNET: HexAddress = HexAddress("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303")

#: HLP (Hyperliquidity Provider) vault address on testnet.
#:
#: Testnet equivalent of :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
#:
#: Source: Hyperliquid ``vaultDetails`` API (testnet)
HLP_VAULT_ADDRESS_TESTNET: HexAddress = HexAddress("0xa15099a30bbf2e68942d6f4c43d70d04faeab0a0")

#: HLP child vault: Strategy A.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_A_ADDRESS: HexAddress = HexAddress("0x010461c14e146ac35fe42271bdc1134ee31c703a")

#: HLP child vault: Strategy B.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_B_ADDRESS: HexAddress = HexAddress("0x31ca8395cf837de08b24da3f660e77761dfb974b")

#: HLP child vault: Strategy X.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_STRATEGY_X_ADDRESS: HexAddress = HexAddress("0x469f690213c467c39a23efacfd2816896009d7d8")

#: HLP child vault: HLP Liquidator.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_ADDRESS: HexAddress = HexAddress("0x2e3d94f0562703b25c83308a05046ddaf9a8dd14")

#: HLP child vault: HLP Liquidator 2.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_2_ADDRESS: HexAddress = HexAddress("0xb0a55f13d22f66e6d495ac98113841b2326e9540")

#: HLP child vault: HLP Liquidator 3.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_3_ADDRESS: HexAddress = HexAddress("0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70")

#: HLP child vault: HLP Liquidator 4.
#:
#: One of the HLP sub-vaults (``relationship_type="child"``).
#: Parent: :py:data:`HLP_VAULT_ADDRESS_MAINNET`.
HLP_LIQUIDATOR_4_ADDRESS: HexAddress = HexAddress("0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308")

#: Standalone Liquidator protocol vault on mainnet.
#:
#: Listed under "Protocol Vaults" on the Hyperliquid UI but has
#: ``relationship_type="normal"`` in the API (not a child of HLP).
#: Leader: ``0xfc13878222c06e7cc043841027c893a4c9f180c9``
#:
#: Source: https://app.hyperliquid.xyz/vaults
LIQUIDATOR_VAULT_ADDRESS: HexAddress = HexAddress("0x63c621a33714ec48660e32f2374895c8026a3a00")

#: All HLP child vault addresses on mainnet.
#:
#: These are the sub-vaults that execute specific strategies
#: on behalf of the HLP parent vault.
HLP_CHILD_VAULT_ADDRESSES: set[HexAddress] = {
    HLP_STRATEGY_A_ADDRESS,
    HLP_STRATEGY_B_ADDRESS,
    HLP_STRATEGY_X_ADDRESS,
    HLP_LIQUIDATOR_ADDRESS,
    HLP_LIQUIDATOR_2_ADDRESS,
    HLP_LIQUIDATOR_3_ADDRESS,
    HLP_LIQUIDATOR_4_ADDRESS,
}

#: Set of all well-known Hyperliquid system vault addresses (mainnet).
#:
#: Includes the HLP parent vault, all HLP child vaults, and the
#: standalone Liquidator protocol vault. These are protocol-operated
#: vaults with special properties (no fees, longer lockup periods,
#: parent/child relationships).
#: Useful for filtering out system vaults from user-created vaults.
HYPERLIQUID_SYSTEM_VAULT_ADDRESSES: set[HexAddress] = {
    HLP_VAULT_ADDRESS_MAINNET,
    LIQUIDATOR_VAULT_ADDRESS,
} | HLP_CHILD_VAULT_ADDRESSES
