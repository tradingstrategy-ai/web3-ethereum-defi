"""Constants for the Hyperliquid integration.

Shared constants used across the Hyperliquid modules
(:py:mod:`~eth_defi.hyperliquid.daily_metrics`,
:py:mod:`~eth_defi.hyperliquid.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

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
