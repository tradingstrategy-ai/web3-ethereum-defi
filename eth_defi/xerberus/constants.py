"""Shared constants for Xerberus integration.

Centralises API URLs, default paths, and configuration values used by
:py:mod:`eth_defi.xerberus.session`, :py:mod:`eth_defi.xerberus.database`,
and :py:mod:`eth_defi.xerberus.scanner`.
"""

import os
from pathlib import Path

#: Xerberus public REST API base URL.
#:
#: All endpoint paths are appended to this, e.g. ``XERBERUS_API_URL + "/registry/scores"``.
XERBERUS_API_URL: str = "https://api.xerberus.io/public/v1"

#: Default DuckDB path for Xerberus risk data.
XERBERUS_DATABASE_PATH: Path = Path("~/.tradingstrategy/vaults/xerberus/xerberus.duckdb").expanduser()

#: Default SQLite database path for rate limiting state.
XERBERUS_RATE_LIMIT_SQLITE_DATABASE: Path = Path("~/.tradingstrategy/vaults/xerberus/rate-limit.sqlite").expanduser()

#: User-Agent header for API requests.
XERBERUS_USER_AGENT: str = "eth-defi/xerberus"

#: Score scale label embedded in every export record.
#:
#: Higher composite scores indicate better risk posture (opposite polarity
#: to Core3 PoL, where lower is safer).
XERBERUS_SCORE_SCALE: str = "0_100_higher_is_better"

#: Platforms accepted by ``GET /vault/list``.
XERBERUS_VAULT_PLATFORMS: tuple[str, ...] = ("ipor", "morpho", "spark", "t3tris")

#: Hard rate-limit cap under the public 10 requests / 60 s quota.
XERBERUS_DEFAULT_REQUESTS_PER_MINUTE: float = 8.0

#: Minimum spacing between authenticated requests (pacing under the minute cap).
XERBERUS_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS: float = 7.5

#: Export max-age for scores (days). Older snapshots are treated as missing.
XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS: int = 30

#: Default HTTP request timeout in seconds.
XERBERUS_DEFAULT_TIMEOUT: float = 60.0

#: Default cap for report URL backfill per run.
XERBERUS_DEFAULT_REPORT_LIMIT: int = 50


def resolve_xerberus_database_path() -> Path:
    """Resolve the Xerberus DuckDB database path.

    Scripts share the same ``XERBERUS_DATABASE_PATH`` environment variable
    override. Keeping the resolver next to :py:data:`XERBERUS_DATABASE_PATH`
    avoids each caller repeating the same environment lookup.

    :return:
        Path from ``XERBERUS_DATABASE_PATH`` or the default DuckDB path.
    """
    path = os.environ.get("XERBERUS_DATABASE_PATH")
    return Path(path).expanduser() if path else XERBERUS_DATABASE_PATH


def resolve_xerberus_api_email() -> str | None:
    """Resolve the registered email for Xerberus API auth from the environment.

    Xerberus requires the email registered with the API key on every
    authenticated request (``x-user-email`` header). This helper only reads
    ``XERBERUS_API_EMAIL``.

    It does **not** invent, default, or derive an email from git config,
    hostname, or other heuristics. Callers and agents must not guess the
    value: if this returns ``None``, the operator must set the env var or
    pass ``api_email=`` explicitly into
    :py:func:`~eth_defi.xerberus.session.create_xerberus_session`.

    See ``README-xerberus.md`` (Authentication section).

    :return:
        Email string from ``XERBERUS_API_EMAIL``, or ``None`` if unset.
    """
    email = os.environ.get("XERBERUS_API_EMAIL")
    if email is not None:
        email = email.strip() or None
    return email
