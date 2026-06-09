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

#: GRVT public GraphQL API endpoint.
#:
#: Used for vault listing with full metadata including per-vault fees
#: (``managementFee``, ``performanceFee``). No authentication required.
#: Fee values are in parts per million (PPM): 10000 = 1%, 200000 = 20%.
GRVT_GRAPHQL_URL = "https://edge.grvt.io/query"

#: GRVT public market data API base URL.
#:
#: Used for public endpoints: ``vault_detail``, ``vault_performance``,
#: ``vault_risk_metric``, ``vault_summary_history``.
#: No authentication required.
GRVT_MARKET_DATA_URL = "https://market-data.grvt.io"

#: GRVT testnet API base URL.
GRVT_TESTNET_API_URL = "https://edge.testnet.grvt.io"

#: Default rate limit for GRVT API requests per second.
#:
#: Conservative estimate. Adjust based on actual rate limit documentation.
GRVT_DEFAULT_REQUESTS_PER_SECOND: float = 2.0

#: Fee mode for GRVT native vaults.
#:
#: Management fees (0-4%) are paid daily via newly minted strategy shares,
#: diluting existing holders — already reflected in the share price.
#: Performance fees (0-40%) are charged on gains at redemption time
#: and NOT reflected in the share price.
#:
#: We use ``externalised`` because performance fees are deducted at
#: redemption — the share price is gross of performance fees.
#: Per-vault fee percentages are fetched from the public GraphQL API
#: at :py:data:`GRVT_GRAPHQL_URL` (``managementFee``, ``performanceFee``
#: fields in PPM: 10000 = 1%).
#:
#: Sources:
#:
#: - `Core concepts <https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts>`__
#: - `Fee setup guide <https://help.grvt.io/en/articles/11640733-strategy-setup-guide-how-to-configure-fees-redemptions-and-rewards-on-grvt>`__
GRVT_VAULT_FEE_MODE: VaultFeeMode = VaultFeeMode.externalised

#: Parts per million divisor for GRVT fee values.
#:
#: GRVT GraphQL API returns fee values in PPM (parts per million).
#: Divide by this constant to get a decimal fraction (e.g. 200000 / 1000000 = 0.20 = 20%).
GRVT_FEE_PPM_DIVISOR: int = 1_000_000

#: Position update interval for GRVT vaults.
#:
#: Investor positions are updated every 4 hours.
#: This acts as a practical lockup/settlement period.
#:
#: Source: https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts
GRVT_VAULT_LOCKUP: datetime.timedelta = datetime.timedelta(hours=4)

#: Maximum age of the stored extended vault info before it is refreshed.
#:
#: The extended vault info (raw GraphQL ``managerInfo`` and other metadata,
#: stored as a JSON dump in the ``extended_vault_info`` column) changes rarely,
#: so it is only re-written when the existing
#: ``extended_vault_info_metadata_last_updated_at`` timestamp is older than this
#: window.  This avoids churning the column on every daily scan.
GRVT_EXTENDED_INFO_MAX_AGE: datetime.timedelta = datetime.timedelta(days=7)

#: Set of GRVT system vault addresses (protocol-curated).
#:
#: The GLP (Grvt Liquidity Provider) is GRVT's own in-house market making
#: vault, operated by the protocol rather than a third-party manager.
#: The synthetic vault address is the lowercased GRVT ``vault_id``
#: (``VLT:...``) as produced by
#: :py:func:`eth_defi.grvt.vault_data_export.create_grvt_vault_row`.
#:
#: Useful for filtering protocol-operated vaults from third-party
#: launchpad vaults, and consumed by
#: :py:func:`eth_defi.vault.curator.identify_curator`.
GRVT_SYSTEM_VAULT_ADDRESSES: set[str] = {
    "vlt:34dtzyg6lhkgm49je5aabi9tebw",  # GLP (Grvt Liquidity Provider)
}
