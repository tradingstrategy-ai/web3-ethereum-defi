"""Shared constants for Core3 integration.

Centralises API URLs, default paths, and configuration values used
by :py:mod:`eth_defi.core3.session`, :py:mod:`eth_defi.core3.database`,
and :py:mod:`eth_defi.core3.scanner`.
"""

import os
from pathlib import Path

#: Core3 Projects Data API base URL.
#:
#: All endpoint paths are appended to this, e.g. ``CORE3_API_URL + "/v1/list"``.
CORE3_API_URL: str = "https://api.core3.io/projects_data"

#: Default DuckDB path for Core3 risk data.
CORE3_DATABASE_PATH: Path = Path("~/.tradingstrategy/vaults/core3/core3.duckdb").expanduser()


def resolve_core3_database_path() -> Path:
    """Resolve the Core3 DuckDB database path.

    Core3 scripts share the same ``CORE3_DATABASE_PATH`` environment
    variable override. Keeping the resolver next to
    :py:data:`CORE3_DATABASE_PATH` avoids each caller repeating the same
    environment lookup and path expansion logic.

    :return:
        Path from ``CORE3_DATABASE_PATH`` or the default Core3 DuckDB path.
    """
    path = os.environ.get("CORE3_DATABASE_PATH")
    return Path(path).expanduser() if path else CORE3_DATABASE_PATH


#: Default SQLite database path for rate limiting state.
#:
#: Using SQLite ensures thread-safe rate limiting across multiple threads
#: when using ``joblib.Parallel`` or similar parallel processing.
CORE3_RATE_LIMIT_SQLITE_DATABASE: Path = Path("~/.tradingstrategy/vaults/core3/rate-limit.sqlite").expanduser()

#: Default requests per second.
#:
#: Conservative; Core3 docs do not specify a quota. The API is behind
#: Cloudflare so aggressive rates may trigger blocks.
CORE3_DEFAULT_REQUESTS_PER_SECOND: float = 5.0

#: User-Agent header required by Cloudflare.
#:
#: Requests without a User-Agent get blocked with HTTP 403 / error code 1010.
CORE3_USER_AGENT: str = "eth-defi/core3"

#: Default HTTP request timeout in seconds.
#:
#: Core3 history endpoints can be slow when backfilling large ranges, so we
#: allow a generous per-request timeout.
CORE3_DEFAULT_TIMEOUT: float = 60.0

#: Project detail sections available for section snapshot fetching.
SECTIONS: tuple[str, ...] = ("security", "financial", "operational", "reputational", "regulatory")

#: Special slug used for the index-level aggregate PoL in the ``pol_daily`` table.
INDEX_SLUG: str = "__index__"
