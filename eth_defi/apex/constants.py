"""Constants for the ApeX Omni vault reader."""

import datetime
from pathlib import Path

#: Synthetic chain identifier for ApeX native vaults.
APEX_CHAIN_ID: int = 9995

#: ApeX Omni public REST API base URL.
APEX_API_BASE_URL: str = "https://omni.apex.exchange/api/v3"

#: Default local metrics database.
APEX_METRICS_DATABASE: Path = Path("~/.tradingstrategy/vaults/apex-vaults.duckdb").expanduser()

#: Default ranking observation cadence.
APEX_DEFAULT_SCAN_INTERVAL: datetime.timedelta = datetime.timedelta(hours=4)

#: Default historical refresh cadence.
APEX_DEFAULT_HISTORY_INTERVAL: datetime.timedelta = datetime.timedelta(hours=24)

#: Default process-wide public API request rate.
APEX_DEFAULT_REQUESTS_PER_SECOND: float = 5.0

#: Default number of history reader workers.
APEX_DEFAULT_MAX_WORKERS: int = 8

#: Default TCP connection timeout.
APEX_DEFAULT_CONNECT_TIMEOUT: float = 10.0

#: Default socket inactivity timeout.
APEX_DEFAULT_READ_TIMEOUT: float = 30.0

#: Maximum duration of one HTTP request attempt.
APEX_DEFAULT_REQUEST_DEADLINE: float = 60.0

#: Maximum duration of the complete two-pass ranking read.
APEX_DEFAULT_RANKING_DEADLINE: float = 300.0

#: Maximum duration of one vault's complete history operation.
APEX_DEFAULT_HISTORY_DEADLINE: float = 120.0

#: Maximum retry sleep.
APEX_DEFAULT_MAX_RETRY_DELAY: float = 10.0

#: Maximum JSON response size.
APEX_DEFAULT_MAX_RESPONSE_BYTES: int = 16 * 1024 * 1024

#: Number of HTTP retries after the initial request.
APEX_DEFAULT_RETRIES: int = 3

#: Number of complete ranking stabilisation retries.
APEX_DEFAULT_RANKING_ATTEMPTS: int = 3

#: Ranking page size verified against the public endpoint.
APEX_RANKING_PAGE_SIZE: int = 100

#: Explicitly verified terminal status.
APEX_TERMINAL_STATUS: str = "VAULT_FINISHED"
