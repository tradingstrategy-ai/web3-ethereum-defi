"""Constants for the GRVT integration.

Shared constants used across the GRVT modules
(:py:mod:`~eth_defi.grvt.daily_metrics`,
:py:mod:`~eth_defi.grvt.vault_data_export`, etc.).
"""

import datetime
from pathlib import Path

from eth_defi.vault.fee import VaultFeeMode

#: GRVT chain ID.
#:
#: Added to :py:data:`eth_defi.chain.CHAIN_NAMES` as ``325: "GRVT"``.
GRVT_CHAIN_ID: int = 325

#: Default path for GRVT daily metrics DuckDB database.
GRVT_DAILY_METRICS_DATABASE = Path.home() / ".tradingstrategy" / "vaults" / "grvt-vaults.duckdb"

#: GRVT authenticated API base URL (requires API key).
#:
#: Used for private endpoints like ``vault_investor_summary`` and
#: ``vault_manager_investor_history``.
GRVT_API_URL = "https://edge.grvt.io"

#: GRVT public market data API base URL.
#:
#: Used for public endpoints: ``vault_detail``, ``vault_performance``,
#: ``vault_risk_metric``, ``vault_summary_history``.
#: No authentication required.
GRVT_MARKET_DATA_URL = "https://market-data.grvt.io"

#: GRVT website URL for scraping vault listings.
#:
#: The strategies page renders vault metadata via Next.js SSR into
#: ``__NEXT_DATA__`` JSON, which we parse to discover all vaults.
GRVT_STRATEGIES_URL = "https://grvt.io/exchange/strategies"

#: GRVT testnet API base URL.
GRVT_TESTNET_API_URL = "https://edge.testnet.grvt.io"

#: Default rate limit for GRVT API requests per second.
#:
#: Conservative estimate. Adjust based on actual rate limit documentation.
GRVT_DEFAULT_REQUESTS_PER_SECOND: float = 2.0

#: Fee mode for GRVT native vaults.
#:
#: Management and performance fees are embedded in the LP token price
#: (deducted from investor returns), so gross and net returns are identical
#: from the pipeline's perspective.
GRVT_VAULT_FEE_MODE: VaultFeeMode = VaultFeeMode.internalised_skimming

#: Position update interval for GRVT vaults.
#:
#: Investor positions are updated every 4 hours.
#: This acts as a practical lockup/settlement period.
#:
#: Source: https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts
GRVT_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(hours=4)
