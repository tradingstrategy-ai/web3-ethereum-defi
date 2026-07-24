"""Environment configuration for the ApeX vault reader."""

import datetime
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from eth_defi.apex.constants import (
    APEX_DEFAULT_CONNECT_TIMEOUT,
    APEX_DEFAULT_HISTORY_DEADLINE,
    APEX_DEFAULT_HISTORY_INTERVAL,
    APEX_DEFAULT_MAX_RESPONSE_BYTES,
    APEX_DEFAULT_MAX_RETRY_DELAY,
    APEX_DEFAULT_MAX_WORKERS,
    APEX_DEFAULT_RANKING_DEADLINE,
    APEX_DEFAULT_READ_TIMEOUT,
    APEX_DEFAULT_REQUEST_DEADLINE,
    APEX_DEFAULT_REQUESTS_PER_SECOND,
    APEX_DEFAULT_SCAN_INTERVAL,
    APEX_METRICS_DATABASE,
)

HistoryMode = Literal["incremental", "refresh", "none"]

_DURATION_RE = re.compile(r"^(?P<value>(?:\d+(?:\.\d*)?|\.\d+))(?P<unit>[smhd])$")
_DURATION_SECONDS = {
    "s": 1.0,
    "m": 60.0,
    "h": 60.0 * 60.0,
    "d": 24.0 * 60.0 * 60.0,
}


def parse_apex_duration(value: str) -> datetime.timedelta:
    """Parse a strictly positive ApeX scheduling duration.

    The parser accepts decimal seconds, minutes, hours and days. Scheduling
    cadence is deliberately independent of the database timestamp schema.

    :param value:
        Duration such as ``30s``, ``30m``, ``1.5h`` or ``2d``.
    :return:
        Parsed positive duration.
    :raise ValueError:
        The value is malformed, unsupported or non-positive.
    """
    match = _DURATION_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Invalid duration {value!r}; expected a positive number followed by s, m, h or d")
    amount = float(match.group("value"))
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError(f"Duration must be finite and positive: {value!r}")
    return datetime.timedelta(seconds=amount * _DURATION_SECONDS[match.group("unit")])


def _parse_positive_float(environ: Mapping[str, str], name: str, default: float) -> float:
    """Parse one finite positive floating-point environment value.

    Missing values fall back to the supplied protocol default before the same
    finite-positive validation is applied.

    :param environ:
        Environment mapping to read.
    :param name:
        Environment variable name.
    :param default:
        Fallback numeric value.
    :return:
        Parsed finite positive value.
    """
    raw = environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive, got {raw!r}")
    return value


def _parse_positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    """Parse one positive integer environment value.

    Missing values fall back to the supplied protocol default and malformed or
    non-positive values fail configuration before any network access.

    :param environ:
        Environment mapping to read.
    :param name:
        Environment variable name.
    :param default:
        Fallback integer value.
    :return:
        Parsed positive integer.
    """
    raw = environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {raw!r}")
    return value


def _parse_bool(environ: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    """Parse a conventional boolean environment value.

    Both numeric and textual true/false spellings are accepted so Compose and
    direct shell invocations behave consistently.

    :param environ:
        Environment mapping to read.
    :param name:
        Environment variable name.
    :param default:
        Value used when the variable is absent.
    :return:
        Parsed boolean value.
    """
    raw = environ.get(name)
    if raw is None:
        return default
    normalised = raw.strip().lower()
    if normalised in {"1", "true", "yes"}:
        return True
    if normalised in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of 1, 0, true, false, yes or no")


@dataclass(slots=True, frozen=True)
class ApexReaderConfig:
    """Validated standalone ApeX reader configuration."""

    #: Console log level.
    log_level: str

    #: DuckDB database path.
    db_path: Path

    #: Optional targeted vault IDs.
    vault_ids: tuple[str, ...] | None

    #: History HTTP worker count.
    max_workers: int

    #: Process-wide public API request rate.
    requests_per_second: float

    #: TCP connection timeout in seconds.
    connect_timeout: float

    #: Socket inactivity timeout in seconds.
    read_timeout: float

    #: Per-request monotonic operation budget in seconds.
    request_deadline: float

    #: Whole-ranking monotonic operation budget in seconds.
    ranking_deadline: float

    #: Per-vault history monotonic operation budget in seconds.
    history_deadline: float

    #: Maximum retry sleep in seconds.
    max_retry_delay: float

    #: Maximum JSON response size.
    max_response_bytes: int

    #: History scan mode.
    history_mode: HistoryMode

    #: History maintenance cadence.
    history_refresh_interval: datetime.timedelta

    #: Whether the standalone command repeats.
    loop: bool

    #: Ranking scan cadence owned by the command.
    scan_interval: datetime.timedelta

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ApexReaderConfig":
        """Build configuration from environment variables.

        :param environ:
            Environment mapping. Defaults to :py:data:`os.environ`.
        :return:
            Fully validated reader configuration.
        """
        env = os.environ if environ is None else environ
        raw_vault_ids = env.get("VAULT_IDS", "").strip()
        vault_ids = tuple(dict.fromkeys(part.strip() for part in raw_vault_ids.split(",") if part.strip())) or None
        history_mode = env.get("HISTORY_MODE", "incremental").strip().lower()
        if history_mode not in {"incremental", "refresh", "none"}:
            raise ValueError(f"Invalid HISTORY_MODE: {history_mode!r}")
        return cls(
            log_level=env.get("LOG_LEVEL", "info"),
            db_path=Path(env.get("DB_PATH", str(APEX_METRICS_DATABASE))).expanduser(),
            vault_ids=vault_ids,
            max_workers=_parse_positive_int(env, "MAX_WORKERS", APEX_DEFAULT_MAX_WORKERS),
            requests_per_second=_parse_positive_float(env, "REQUESTS_PER_SECOND", APEX_DEFAULT_REQUESTS_PER_SECOND),
            connect_timeout=_parse_positive_float(env, "CONNECT_TIMEOUT", APEX_DEFAULT_CONNECT_TIMEOUT),
            read_timeout=_parse_positive_float(env, "READ_TIMEOUT", APEX_DEFAULT_READ_TIMEOUT),
            request_deadline=_parse_positive_float(env, "REQUEST_DEADLINE", APEX_DEFAULT_REQUEST_DEADLINE),
            ranking_deadline=_parse_positive_float(env, "RANKING_DEADLINE", APEX_DEFAULT_RANKING_DEADLINE),
            history_deadline=_parse_positive_float(env, "HISTORY_DEADLINE", APEX_DEFAULT_HISTORY_DEADLINE),
            max_retry_delay=_parse_positive_float(env, "MAX_RETRY_DELAY", APEX_DEFAULT_MAX_RETRY_DELAY),
            max_response_bytes=_parse_positive_int(env, "MAX_RESPONSE_BYTES", APEX_DEFAULT_MAX_RESPONSE_BYTES),
            history_mode=history_mode,
            history_refresh_interval=parse_apex_duration(env.get("HISTORY_REFRESH_INTERVAL", f"{APEX_DEFAULT_HISTORY_INTERVAL.total_seconds() / 3600:g}h")),
            loop=_parse_bool(env, "LOOP"),
            scan_interval=parse_apex_duration(env.get("SCAN_INTERVAL", f"{APEX_DEFAULT_SCAN_INTERVAL.total_seconds() / 3600:g}h")),
        )
