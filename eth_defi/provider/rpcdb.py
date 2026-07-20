"""Reusable JSON-RPC request accounting and DuckDB persistence.

The counters in this module sit below any particular scanner. A caller creates
one :class:`RPCRequestStats` for a logical phase, passes it to Web3 providers,
and persists the completed aggregate with :class:`RPCUsageDatabase`.

The fixed DuckDB schema retains the historical ``vault_rpc_api_*`` table names,
but ``phase`` and ``items_scanned`` are intentionally generic. For example, a
block indexer may use ``phase="block_index"`` and define ``items_scanned`` as
the number of indexed blocks without importing any vault package.
"""

from __future__ import annotations

import datetime
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from requests import HTTPError
from tabulate import tabulate

try:
    import duckdb
except ImportError:
    duckdb = None

from eth_defi.utils import get_url_domain

#: Default shared DuckDB path for JSON-RPC request accounting.
DEFAULT_RPC_TRACKING_DATABASE = Path.home() / ".tradingstrategy" / "rpc-tracking.duckdb"

#: Environment variable overriding :data:`DEFAULT_RPC_TRACKING_DATABASE`.
RPC_TRACKING_DATABASE_PATH_ENV = "RPC_TRACKING_DATABASE_PATH"

#: Maximum stored error-message length after sanitisation.
MAX_RPC_ERROR_MESSAGE_LENGTH = 500

#: Marker values used to preserve a completed zero-call scan iteration.
ZERO_CALL_MARKER = "none"

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)\b(api[-_ ]?key|token|authorization|secret)\b\s*[:=]\s*[^\s,;}]+")
_TRACE_RE = re.compile(r"(?i)\b(trace[-_ ]?id|request[-_ ]?id)\b\s*[:=]\s*['\"]?[^\s,'\";}]+")
_HEX_DATA_RE = re.compile(r"\b0x[a-fA-F0-9]{64,}\b")
_WHITESPACE_RE = re.compile(r"\s+")


def resolve_rpc_tracking_database_path() -> Path:
    """Resolve the shared JSON-RPC tracking DuckDB path.

    The resolver follows the same convention as other repository DuckDB
    modules: a module-level default below ``~/.tradingstrategy`` and an
    environment-variable override with user-home expansion.

    :return:
        Expanded path from ``RPC_TRACKING_DATABASE_PATH`` or the default path.
    """

    path = os.environ.get(RPC_TRACKING_DATABASE_PATH_ENV)
    return Path(path).expanduser() if path else DEFAULT_RPC_TRACKING_DATABASE


def sanitise_rpc_error_message(message: str) -> str:
    """Remove secrets and high-cardinality identifiers from an RPC error.

    Endpoint URLs are reduced to their provider domain, common secret fields
    and request identifiers are redacted, whitespace is collapsed, and the
    stored value is capped to a stable maximum length.

    :param message:
        Raw exception or JSON-RPC response message. Request parameters should
        not be included by callers.

    :return:
        Safe, compact message suitable for a DuckDB aggregation key.
    """

    def _replace_url(match: re.Match[str]) -> str:
        raw_url = match.group(0).rstrip(".,);]}")
        try:
            domain = get_url_domain(raw_url)
        except (TypeError, ValueError):
            domain = None
        return f"<rpc:{domain}>" if domain else "<rpc-url>"

    clean = _URL_RE.sub(_replace_url, str(message))
    clean = _SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", clean)
    clean = _TRACE_RE.sub(lambda match: f"{match.group(1)}=<redacted>", clean)
    clean = _HEX_DATA_RE.sub("<hex-data>", clean)
    clean = _WHITESPACE_RE.sub(" ", clean).strip()
    return clean[:MAX_RPC_ERROR_MESSAGE_LENGTH] or "unknown"


def normalise_rpc_error(error: BaseException | dict[str, Any]) -> tuple[str, str]:
    """Convert heterogeneous provider failures to stable aggregation values.

    JSON-RPC response dictionaries use their numeric error code. HTTP failures
    use ``http_<status>``. Other Python failures use the concrete exception
    class name. The returned message is always sanitised.

    :param error:
        A JSON-RPC error dictionary or an exception raised by the provider.

    :return:
        ``(error_code, error_message)`` suitable for
        :meth:`RPCRequestStats.record_error`.
    """

    payload: dict[str, Any] | None = error if isinstance(error, dict) else None
    if payload is None and isinstance(error, BaseException) and error.args and isinstance(error.args[0], dict):
        payload = error.args[0]

    if payload is not None:
        code = str(payload.get("code", "unknown"))
        message = str(payload.get("message", payload))
        return code, sanitise_rpc_error_message(message)

    if isinstance(error, HTTPError) and error.response is not None:
        code = f"http_{error.response.status_code}"
    elif isinstance(error, BaseException):
        code = error.__class__.__name__
    else:
        code = "unknown"

    return code, sanitise_rpc_error_message(str(error))


@dataclass(slots=True)
class RPCRequestStats:
    """Thread-safe, pickle-safe physical JSON-RPC request counters.

    ``calls`` is keyed by ``(rpc_provider_domain, api_call)`` and ``errors`` by
    ``(rpc_provider_domain, error_code, error_message)``. The lock is excluded
    from pickle state and recreated in subprocesses.

    :param calls:
        Initial provider-domain and method counts.
    :param errors:
        Initial provider-domain and normalised error counts.
    """

    calls: Counter[tuple[str, str]] = field(default_factory=Counter)
    errors: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def record_call(self, rpc_provider_domain: str, api_call: str, count: int = 1) -> None:
        """Record physical JSON-RPC request attempts.

        :param rpc_provider_domain:
            Safe provider hostname, optionally including a non-default port.
        :param api_call:
            JSON-RPC method name such as ``eth_call``.
        :param count:
            Positive number of attempts to add.
        """

        assert rpc_provider_domain, "RPC provider domain must not be empty"
        assert api_call, "JSON-RPC method must not be empty"
        assert count > 0, f"Count must be positive: {count}"
        with self._lock:
            self.calls[rpc_provider_domain, str(api_call)] += count

    def record_error(self, rpc_provider_domain: str, error_code: str, error_message: str, count: int = 1) -> None:
        """Record normalised JSON-RPC request failures.

        :param rpc_provider_domain:
            Safe provider hostname, optionally including a non-default port.
        :param error_code:
            Stable JSON-RPC, HTTP, or exception-class error code.
        :param error_message:
            Sanitised error message.
        :param count:
            Positive number of matching failures to add.
        """

        assert rpc_provider_domain, "RPC provider domain must not be empty"
        assert error_code, "RPC error code must not be empty"
        assert count > 0, f"Count must be positive: {count}"
        safe_message = sanitise_rpc_error_message(error_message)
        with self._lock:
            self.errors[rpc_provider_domain, str(error_code), safe_message] += count

    def merge(self, other: RPCRequestStats) -> None:
        """Merge another worker or phase aggregate exactly once.

        :param other:
            Detached task statistics to add to this accumulator.
        """

        assert isinstance(other, RPCRequestStats), f"Expected RPCRequestStats, got {type(other)}"
        other_calls, other_errors = other.export()
        with self._lock:
            self.calls.update(other_calls)
            self.errors.update(other_errors)

    def export(self) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str, str]]]:
        """Take a detached copy of both counter mappings.

        :return:
            Copied call and error counters safe to iterate without holding the
            accumulator lock.
        """

        with self._lock:
            return self.calls.copy(), self.errors.copy()

    def __getstate__(self) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]]:
        """Serialise counters without the non-pickleable thread lock."""

        calls, errors = self.export()
        return dict(calls), dict(errors)

    def __setstate__(self, state: tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]]) -> None:
        """Restore counters and create a process-local thread lock."""

        calls, errors = state
        self.calls = Counter(calls)
        self.errors = Counter(errors)
        self._lock = threading.Lock()


class RPCUsageDatabase:
    """Append-only DuckDB storage for JSON-RPC scan accounting.

    One connection belongs to one externally serialised writer. Callers sharing
    a database across processes must hold their pipeline lock for the complete
    connection lifetime.
    """

    def __init__(self, path: Path) -> None:
        """Open the tracking database and initialise its fixed schema.

        :param path:
            DuckDB file path. Parent directories are created automatically.
        """

        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected database file path, got directory: {path}"
        if duckdb is None:
            message = "Install eth-defi with the 'duckdb' extra to use RPCUsageDatabase"
            raise ImportError(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection: duckdb.DuckDBPyConnection | None = duckdb.connect(str(path))
        self._create_schema()

    def _create_schema(self) -> None:
        """Create the fixed call and error aggregation tables."""

        connection = self._require_connection()
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vault_rpc_api_calls (
                chain INTEGER NOT NULL,
                phase VARCHAR NOT NULL,
                api_call VARCHAR NOT NULL,
                cycle_started DATE NOT NULL,
                cycle_number INTEGER NOT NULL,
                rpc_provider_domain VARCHAR NOT NULL,
                call_count UBIGINT NOT NULL,
                items_scanned INTEGER NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS vault_rpc_api_errors (
                chain INTEGER NOT NULL,
                phase VARCHAR NOT NULL,
                cycle_started DATE NOT NULL,
                cycle_number INTEGER NOT NULL,
                rpc_provider_domain VARCHAR NOT NULL,
                error_code VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL,
                error_count UBIGINT NOT NULL
            )
        """)

    def _require_connection(self) -> duckdb.DuckDBPyConnection:
        """Return the open connection or fail after explicit close.

        :return:
            Active DuckDB connection.
        """

        if self.connection is None:
            raise RuntimeError(f"RPC usage database is closed: {self.path}")
        return self.connection

    def allocate_cycle(self) -> int:
        """Allocate the next persistent cycle number.

        Allocation uses both tables and must be called while the external
        pipeline-writer lock is held. A crash before the first row is inserted
        may reuse the unpersisted number on the next invocation.

        :return:
            Next positive cycle number.
        """

        row = (
            self._require_connection()
            .execute("""
            SELECT coalesce(max(cycle_number), 0) + 1
            FROM (
                SELECT cycle_number FROM vault_rpc_api_calls
                UNION ALL
                SELECT cycle_number FROM vault_rpc_api_errors
            )
        """)
            .fetchone()
        )
        return int(row[0])

    def record_scan(  # noqa: PLR0917
        self,
        chain: int,
        phase: str,
        cycle_started: datetime.date,
        cycle_number: int,
        stats: RPCRequestStats,
        items_scanned: int,
    ) -> None:
        """Append one completed scan-attempt aggregate atomically.

        Call and error rows are committed in the same transaction. An empty
        call aggregate writes a zero-count marker so the scan iteration and its
        item count remain visible. Unknown item counts on early failures should
        be passed as zero.

        :param chain:
            EVM chain id.
        :param phase:
            Caller-defined scan phase, for example ``lead_discovery``.
        :param cycle_started:
            Naive UTC calendar date on which the logical cycle started.
        :param cycle_number:
            Persistent cycle identifier shared by scanner retries.
        :param stats:
            Physical request and error counters for this attempt only.
        :param items_scanned:
            Non-negative number of logical items submitted during the attempt.
        """

        assert chain > 0, f"Invalid EVM chain id: {chain}"
        assert phase, "Phase must not be empty"
        assert isinstance(cycle_started, datetime.date), f"Expected date, got {type(cycle_started)}"
        assert cycle_number > 0, f"Invalid cycle number: {cycle_number}"
        assert isinstance(stats, RPCRequestStats), f"Expected RPCRequestStats, got {type(stats)}"
        assert items_scanned >= 0, f"Items scanned must not be negative: {items_scanned}"

        calls, errors = stats.export()
        call_rows = [(chain, phase, api_call, cycle_started, cycle_number, provider_domain, count, items_scanned) for (provider_domain, api_call), count in sorted(calls.items())]
        if not call_rows:
            call_rows.append((chain, phase, ZERO_CALL_MARKER, cycle_started, cycle_number, ZERO_CALL_MARKER, 0, items_scanned))

        error_rows = [(chain, phase, cycle_started, cycle_number, provider_domain, error_code, error_message, count) for (provider_domain, error_code, error_message), count in sorted(errors.items())]

        connection = self._require_connection()
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.executemany(
                "INSERT INTO vault_rpc_api_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                call_rows,
            )
            if error_rows:
                connection.executemany(
                    "INSERT INTO vault_rpc_api_errors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    error_rows,
                )
            connection.execute("COMMIT")
        except duckdb.Error:
            connection.execute("ROLLBACK")
            raise

    def fetch_cycle_calls(self, chain: int, cycle_started: datetime.date, cycle_number: int) -> list[tuple[str, str, str, int, int]]:
        """Fetch current-cycle method rows for one chain.

        :param chain:
            EVM chain id.
        :param cycle_started:
            UTC cycle date.
        :param cycle_number:
            Persistent cycle number.

        :return:
            Rows of ``(phase, provider_domain, api_call, call_count,
            items_scanned)`` aggregated across retry attempts.
        """

        return (
            self._require_connection()
            .execute(
                """
            SELECT phase, rpc_provider_domain, api_call,
                   sum(call_count)::UBIGINT AS call_count,
                   max(items_scanned)::INTEGER AS items_scanned
            FROM vault_rpc_api_calls
            WHERE chain = ? AND cycle_started = ? AND cycle_number = ?
            GROUP BY phase, rpc_provider_domain, api_call
            ORDER BY phase, rpc_provider_domain, api_call
            """,
                [chain, cycle_started, cycle_number],
            )
            .fetchall()
        )

    def fetch_daily_totals(self, chain: int, cycle_started: datetime.date) -> list[tuple[str, str, int, int]]:
        """Fetch daily provider totals without multiplying retry item counts.

        Each cycle contributes the sum of its method rows and the maximum item
        count reported by any retry. Provider rows display provider-specific
        calls with the cycle-level item denominator.

        :return:
            Rows of ``(phase, provider_domain, call_count, items_scanned)``.
        """

        return (
            self._require_connection()
            .execute(
                """
            WITH cycle_items AS (
                SELECT phase, cycle_number, max(items_scanned) AS items_scanned
                FROM vault_rpc_api_calls
                WHERE chain = ? AND cycle_started = ?
                GROUP BY phase, cycle_number
            ), provider_cycles AS (
                SELECT phase, cycle_number, rpc_provider_domain,
                       sum(call_count) AS call_count
                FROM vault_rpc_api_calls
                WHERE chain = ? AND cycle_started = ?
                GROUP BY phase, cycle_number, rpc_provider_domain
            )
            SELECT provider_cycles.phase,
                   provider_cycles.rpc_provider_domain,
                   sum(provider_cycles.call_count)::UBIGINT,
                   sum(cycle_items.items_scanned)::UBIGINT
            FROM provider_cycles
            JOIN cycle_items USING (phase, cycle_number)
            GROUP BY provider_cycles.phase, provider_cycles.rpc_provider_domain
            ORDER BY provider_cycles.phase, provider_cycles.rpc_provider_domain
            """,
                [chain, cycle_started, chain, cycle_started],
            )
            .fetchall()
        )

    def fetch_cycle_errors(self, chain: int, cycle_started: datetime.date, cycle_number: int) -> list[tuple[str, str, str, str, int]]:
        """Fetch current-cycle normalised error totals for one chain.

        :return:
            Rows of ``(phase, provider_domain, error_code, error_message,
            error_count)``.
        """

        return (
            self._require_connection()
            .execute(
                """
            SELECT phase, rpc_provider_domain, error_code, error_message,
                   sum(error_count)::UBIGINT
            FROM vault_rpc_api_errors
            WHERE chain = ? AND cycle_started = ? AND cycle_number = ?
            GROUP BY phase, rpc_provider_domain, error_code, error_message
            ORDER BY phase, rpc_provider_domain, error_code, error_message
            """,
                [chain, cycle_started, cycle_number],
            )
            .fetchall()
        )

    def close(self) -> None:
        """Checkpoint and close the DuckDB connection explicitly."""

        if self.connection is not None:
            connection = self.connection
            self.connection = None
            try:
                connection.execute("CHECKPOINT")
            finally:
                connection.close()

    def __enter__(self) -> Self:
        """Return this database for a managed connection lifetime."""

        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: Any) -> None:
        """Close the database when leaving a managed connection lifetime."""

        self.close()


def format_rpc_usage_report(database: RPCUsageDatabase, chain: int, cycle_started: datetime.date, cycle_number: int) -> str:
    """Format current-cycle and daily JSON-RPC usage for one chain.

    The formatter is output-system agnostic: scanners may print the returned
    text, log it, or embed it in another report. Daily rows keep lead discovery
    and price scanning separate through their ``phase`` column.

    :param database:
        Open tracking database.
    :param chain:
        EVM chain id to report.
    :param cycle_started:
        UTC date of the current logical cycle.
    :param cycle_number:
        Current persistent cycle number.

    :return:
        Multi-table plain-text report.
    """

    cycle_calls = database.fetch_cycle_calls(chain, cycle_started, cycle_number)
    phase_totals_by_phase: dict[str, tuple[int, int]] = {}
    for phase, _provider, _api_call, call_count, items_scanned in cycle_calls:
        previous_calls, previous_items = phase_totals_by_phase.get(phase, (0, 0))
        phase_totals_by_phase[phase] = previous_calls + call_count, max(previous_items, items_scanned)
    phase_totals = [(phase, *totals) for phase, totals in phase_totals_by_phase.items()]
    daily_totals = database.fetch_daily_totals(chain, cycle_started)
    cycle_errors = database.fetch_cycle_errors(chain, cycle_started, cycle_number)

    sections = [f"JSON-RPC usage for chain {chain}, cycle {cycle_number} ({cycle_started.isoformat()})"]
    sections.append(
        tabulate(
            cycle_calls,
            headers=("Phase", "Provider", "API call", "Calls", "Items scanned"),
            tablefmt="simple",
        )
        if cycle_calls
        else "No current-cycle JSON-RPC usage rows"
    )
    if phase_totals:
        sections.extend(
            (
                "Current-cycle phase totals",
                tabulate(phase_totals, headers=("Phase", "Calls", "Items scanned"), tablefmt="simple"),
            )
        )
    if daily_totals:
        sections.extend(
            (
                "UTC daily-to-date totals",
                tabulate(daily_totals, headers=("Phase", "Provider", "Calls", "Items scanned"), tablefmt="simple"),
            )
        )
    if cycle_errors:
        sections.extend(
            (
                "Current-cycle RPC errors",
                tabulate(cycle_errors, headers=("Phase", "Provider", "Code", "Message", "Errors"), tablefmt="simple"),
            )
        )
    return "\n\n".join(sections)
