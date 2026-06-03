"""Shared constants for Core3 integration.

Centralises API URLs, default paths, and configuration values used
by :py:mod:`eth_defi.core3.session`, :py:mod:`eth_defi.core3.database`,
and :py:mod:`eth_defi.core3.scanner`.
"""

from pathlib import Path

#: Core3 Projects Data API base URL.
#:
#: All endpoint paths are appended to this, e.g. ``CORE3_API_URL + "/v1/list"``.
CORE3_API_URL: str = "https://api.core3.io/projects_data"

#: Default DuckDB path for Core3 risk data.
CORE3_DATABASE_PATH: Path = Path("~/.tradingstrategy/core3/risk-data.duckdb").expanduser()

#: Default requests per second.
#:
#: Conservative; Core3 docs do not specify a quota. The API is behind
#: Cloudflare so aggressive rates may trigger blocks.
CORE3_DEFAULT_REQUESTS_PER_SECOND: float = 5.0

#: User-Agent header required by Cloudflare.
#:
#: Requests without a User-Agent get blocked with HTTP 403 / error code 1010.
CORE3_USER_AGENT: str = "eth-defi/core3"

#: Project detail sections available for section snapshot fetching.
SECTIONS: tuple[str, ...] = ("security", "financial", "operational", "reputational", "regulatory")

#: Special slug used for the index-level aggregate PoL in the ``pol_daily`` table.
INDEX_SLUG: str = "__index__"
