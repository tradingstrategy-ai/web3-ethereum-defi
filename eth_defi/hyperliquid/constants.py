"""Constants for the Hyperliquid integration.

Shared constants used across the Hyperliquid modules
(:py:mod:`~eth_defi.hyperliquid.daily_metrics`,
:py:mod:`~eth_defi.hyperliquid.vault_data_export`, etc.).
"""

from pathlib import Path

from eth_defi.vault.fee import VaultFeeMode

#: Synthetic in-house chain ID for Hypercore (Hyperliquid's native non-EVM layer).
#:
#: Uses a negative number to avoid collision with any real EVM chain ID.
#: This is distinct from HyperEVM (chain ID 999) which is the EVM-compatible sidechain.
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``-999: "Hypercore"``.
HYPERCORE_CHAIN_ID: int = -999

#: Default path for Hyperliquid daily metrics DuckDB database.
HYPERLIQUID_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "hyperliquid" / "daily-metrics.duckdb"

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
