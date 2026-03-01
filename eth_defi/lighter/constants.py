"""Constants for the Lighter integration.

Shared constants used across the Lighter modules
(:py:mod:`~eth_defi.lighter.daily_metrics`,
:py:mod:`~eth_defi.lighter.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

from eth_defi.vault.fee import VaultFeeMode

#: Synthetic in-house chain ID for Lighter (ZK-rollup, non-standard EVM).
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``9998: "Lighter"``.
LIGHTER_CHAIN_ID: int = 9998

#: Lighter mainnet API base URL.
LIGHTER_API_URL: str = "https://mainnet.zklighter.elliot.ai"

#: Default path for Lighter daily metrics DuckDB database.
LIGHTER_DAILY_METRICS_DATABASE: Path = Path.home() / ".tradingstrategy" / "vaults" / "lighter-pools.duckdb"

#: Default rate limit for Lighter API requests per second.
#:
#: Conservative estimate based on observed API behaviour.
LIGHTER_DEFAULT_REQUESTS_PER_SECOND: float = 2.0

#: Fee mode for Lighter native pools.
#:
#: Pool operators can set an ``operator_fee`` (0-100%). The share prices
#: from the API already reflect the operator's fee deduction, so the
#: pipeline sees net-of-fees prices. This matches internalised skimming.
LIGHTER_POOL_FEE_MODE: VaultFeeMode = VaultFeeMode.internalised_skimming

#: Pool denomination currency.
#:
#: Lighter uses USDC as the exchange base currency.
LIGHTER_DENOMINATION: str = "USDC"

#: Pool cooldown period for withdrawals.
#:
#: From ``systemConfig.liquidity_pool_cooldown_period`` (300000ms = 5 minutes).
LIGHTER_POOL_LOCKUP: datetime.timedelta = datetime.timedelta(minutes=5)
