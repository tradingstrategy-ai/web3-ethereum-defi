"""Constants for the Hibachi integration.

Shared constants used across the Hibachi modules
(:py:mod:`~eth_defi.hibachi.daily_metrics`,
:py:mod:`~eth_defi.hibachi.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

from eth_defi.vault.fee import VaultFeeMode

#: Hibachi synthetic chain ID.
#:
#: In-house synthetic ID — NOT an EVM JSON-RPC chain ID.
#: 9997 collides with AltLayer Testnet on chainid.network,
#: consistent with how 9998 (Lighter) and 9999 (Hypercore) collide too.
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``9997: "Hibachi"``.
HIBACHI_CHAIN_ID: int = 9997

#: Default path for Hibachi daily metrics DuckDB database.
HIBACHI_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "hibachi-vaults.duckdb"

#: Hibachi public data API base URL.
#:
#: Used for all vault and market data endpoints.
#: No authentication required.
HIBACHI_DATA_API_URL: str = "https://data-api.hibachi.xyz"

#: Fee mode for Hibachi native vaults.
#:
#: All vault-level fees are zero (management, performance, deposit,
#: withdrawal are all ``"0.00000000"`` in the API response).
#: The platform charges trading taker fees (0.045%) and
#: deposit/withdrawal fees separately at the exchange level,
#: but these are not vault-specific.
HIBACHI_VAULT_FEE_MODE: VaultFeeMode = VaultFeeMode.feeless

#: Lockup period for Hibachi vaults.
#:
#: Both vaults report ``minUnlockHours: 0`` — no lockup.
HIBACHI_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(hours=0)
