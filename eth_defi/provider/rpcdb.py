"""JSON-RPC request accounting and DuckDB persistence.

A caller creates one :class:`RPCRequestStats` for a phase, passes it to Web3
providers, and persists the aggregate with :class:`RPCUsageDatabase`.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Self

from requests import HTTPError
from tabulate import tabulate

try:
    import duckdb
except ImportError:
    duckdb = None

#: Default shared DuckDB path for JSON-RPC request accounting.
DEFAULT_RPC_TRACKING_DATABASE = Path.home() / ".tradingstrategy" / "rpc-tracking.duckdb"

#: Environment variable overriding :data:`DEFAULT_RPC_TRACKING_DATABASE`.
RPC_TRACKING_DATABASE_PATH_ENV = "RPC_TRACKING_DATABASE_PATH"

#: Marker value used to preserve a completed zero-call scan iteration.
ZERO_CALL_MARKER = "none"


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


def normalise_rpc_error(error: BaseException | dict[str, Any]) -> tuple[str, str]:
    """Convert heterogeneous provider failures to stable aggregation values.

    JSON-RPC response dictionaries use their numeric error code. HTTP failures
    use ``http_<status>``. Other Python failures use the concrete exception
    class name. The provider's original error message is retained.

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
        return code, message

    if isinstance(error, HTTPError) and error.response is not None:
        code = f"http_{error.response.status_code}"
    elif isinstance(error, BaseException):
        code = error.__class__.__name__
    else:
        code = "unknown"

    return code, str(error)


@dataclass(slots=True)
class RPCRequestStats:
    """Thread-safe, pickle-safe physical JSON-RPC request counters.

    ``calls`` is keyed by ``(rpc_provider_domain, api_call)`` and ``errors`` by
    ``(rpc_provider_domain, error_code, error_message)``. ``operation_calls``
    uses ``(operation, rpc_provider_domain, api_call)`` to attribute those same
    physical attempts to scanner work without increasing the total. Tuple
    positions match the persistence writer's unpacking order. The lock is
    excluded from pickle state and recreated in subprocesses.
    """

    #: Key: ``(rpc_provider_domain, api_call)``; value: physical attempt count.
    #: The domain is the provider hostname with an optional non-default port;
    #: api_call is the JSON-RPC method name, e.g. ``eth_call``.
    calls: Counter[tuple[str, str]] = field(default_factory=Counter)

    #: Key: ``(rpc_provider_domain, error_code, error_message)``; value: failure count.
    #: Codes are normalised by :func:`normalise_rpc_error`; messages retain the
    #: provider's original text so distinct failures remain distinguishable.
    errors: Counter[tuple[str, str, str]] = field(default_factory=Counter)

    #: Key: ``(operation, rpc_provider_domain, api_call)``; value: physical attempt count.
    #: Operation is the scanner work label at request time. These rows describe
    #: the same requests as ``calls`` and must never be added to its totals.
    operation_calls: Counter[tuple[str, str, str]] = field(default_factory=Counter)

    #: Label applied to new physical attempts through this accumulator.
    operation: str = "unclassified"

    #: Synchronises counter updates between worker threads.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    #: Worker-local operation override; never shared between reading threads.
    #: Excluded from pickle and recreated alongside the counter lock.
    _operation_context: threading.local = field(default_factory=threading.local, init=False, repr=False, compare=False)

    @contextmanager
    def operation_scope(self, operation: str) -> Iterator[None]:
        """Attribute a worker's physical requests without changing other workers.

        Multicall isolates reviewed HyperCore targets under an operation suffix.
        A thread-local override keeps shared admission counters race-free, while
        existing counter serialisation carries the resulting partition to parents.

        :param operation: Label for requests issued synchronously in this scope.
        :return: Context manager restoring the prior label on every exit.
        """
        previous = getattr(self._operation_context, "operation", None)
        self._operation_context.operation = operation
        try:
            yield
        finally:
            self._operation_context.operation = previous

    def record_call(self, rpc_provider_domain: str, api_call: str, count: int = 1) -> None:
        """Record physical JSON-RPC request attempts.

        Provider instrumentation calls this for each attempt, including retry
        and failover. Operation labels describe those same attempts; counting
        Multicall subcalls here or adding operation totals would inflate cost.

        :param rpc_provider_domain:
            Provider hostname, optionally including a non-default port.
        :param api_call:
            JSON-RPC method name such as ``eth_call``.
        :param count:
            Positive number of attempts to add.
        :return:
            None; both total and operation counters advance together.
        """

        assert rpc_provider_domain, "RPC provider domain must not be empty"
        assert api_call, "JSON-RPC method must not be empty"
        assert count > 0, f"Count must be positive: {count}"
        with self._lock:
            self.calls[rpc_provider_domain, str(api_call)] += count
            operation = getattr(self._operation_context, "operation", None) or self.operation
            self.operation_calls[operation, rpc_provider_domain, str(api_call)] += count

    def record_error(self, rpc_provider_domain: str, error_code: str, error_message: str, count: int = 1) -> None:
        """Record JSON-RPC request failures.

        :param rpc_provider_domain:
            Provider hostname, optionally including a non-default port.
        :param error_code:
            Stable JSON-RPC, HTTP, or exception-class error code.
        :param error_message:
            Provider error message.
        :param count:
            Positive number of matching failures to add.
        """

        assert rpc_provider_domain, "RPC provider domain must not be empty"
        assert error_code, "RPC error code must not be empty"
        assert count > 0, f"Count must be positive: {count}"
        with self._lock:
            self.errors[rpc_provider_domain, str(error_code), str(error_message)] += count

    def merge(self, other: RPCRequestStats) -> None:
        """Merge another worker or phase aggregate exactly once.

        :param other:
            Detached task statistics to add to this accumulator.
        """

        assert isinstance(other, RPCRequestStats), f"Expected RPCRequestStats, got {type(other)}"
        other_calls, other_errors = other.export()
        with other._lock:
            operations = other.operation_calls.copy()
        with self._lock:
            self.calls.update(other_calls)
            self.errors.update(other_errors)
            self.operation_calls.update(operations)

    def export(self) -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str, str]]]:
        """Take a detached copy of both counter mappings.

        :return:
            ``(calls, errors)`` safe to iterate without holding the accumulator
            lock. Call keys are ``(rpc_provider_domain, api_call)``; error keys
            are ``(rpc_provider_domain, error_code, error_message)``. Values are
            integer attempt and failure counts respectively. Operation counters
            are excluded from this legacy two-counter interface.
        """

        with self._lock:
            return self.calls.copy(), self.errors.copy()

    def __getstate__(self) -> tuple[Any, ...]:
        """Serialise counters without the non-pickleable thread lock.

        Worker processes transport this positional state rather than the lock.
        Counter key layouts remain those documented on the corresponding fields.

        :return:
            ``(calls_dict, errors_dict, operation_calls_dict, operation_label)``.
        """

        with self._lock:
            return dict(self.calls), dict(self.errors), dict(self.operation_calls), self.operation

    def __setstate__(self, state: tuple[Any, ...]) -> None:
        """Restore counters and create a process-local thread lock.

        Older worker payloads contain only the two legacy counters. Missing
        operation fields default to an empty breakdown and an unclassified label.

        :param state:
            ``(calls_dict, errors_dict, operation_calls_dict, operation_label)``
            from :meth:`__getstate__`, or its legacy two-member prefix.
        :return:
            None; counter mappings and the lock are restored on this instance.
        """

        calls, errors = state[:2]
        self.calls = Counter(calls)
        self.errors = Counter(errors)
        self.operation_calls = Counter(state[2]) if len(state) > 2 else Counter()
        self.operation = state[3] if len(state) > 3 else "unclassified"
        self._lock = threading.Lock()
        self._operation_context = threading.local()


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

        connection.execute("""
            CREATE TABLE IF NOT EXISTS vault_rpc_operation_calls (
                chain INTEGER, phase VARCHAR, operation VARCHAR, api_call VARCHAR,
                cycle_started DATE, cycle_number INTEGER, rpc_provider_domain VARCHAR,
                call_count UBIGINT, items_scanned INTEGER, outcome VARCHAR, metrics VARCHAR
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
        metrics: dict | None = None,
    ) -> None:
        """Append one finished phase attempt, including failed or degraded scans.

        Call and error rows are committed in the same transaction. An empty
        call aggregate writes a zero-count marker so the scan iteration and its
        item count remain visible. Unknown item counts on early failures should
        be passed as zero.

        The all-chain phase boundary calls this after its workers have merged
        their counters. Legacy positional schemas remain unchanged for scanner
        rollback; operation labels and outcomes live in a separate detail table.
        A successful function return does not prove full reader coverage, so
        pending candidates and unavailable/overdue readers produce a degraded
        outcome even when the phase did not raise an exception.

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
        :param metrics:
            Optional phase diagnostics stored only in the separate detail table.
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
            with stats._lock:
                detail_rows = [(chain, phase, operation, method, cycle_started, cycle_number, provider, count, items_scanned, None, None) for (operation, provider, method), count in sorted(stats.operation_calls.items())]
            # Outcome rows have no physical requests. They retain zero-call
            # cache hits and distinguish completed work from partial coverage;
            # readers must not add them to the legacy request totals.
            if metrics and metrics.get("error"):
                outcome = "failed"
            elif metrics and (metrics.get("pending_candidates") or metrics.get("reader_unavailable") or metrics.get("low_activity_unverified") or metrics.get("overdue_vaults") or metrics.get("denomination_unavailable_vaults")):
                outcome = "degraded"
            else:
                outcome = "completed"
            safe_metrics = {key: value for key, value in (metrics or {}).items() if key not in {"error", "traceback"}}
            detail_rows.append((chain, phase, "outcome", ZERO_CALL_MARKER, cycle_started, cycle_number, ZERO_CALL_MARKER, 0, items_scanned, outcome, json.dumps(safe_metrics, default=str)))
            connection.executemany("INSERT INTO vault_rpc_operation_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", detail_rows)
            connection.execute("COMMIT")
        except BaseException:
            # A serialization error or interruption after legacy inserts must
            # roll back the detail rows too. Otherwise the shared connection
            # retains an open transaction and later accounting attempts fail.
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
        """Fetch current-cycle error totals for one chain.

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
    # Phase name maps to (summed physical attempts, maximum items scanned).
    # Each provider/method row repeats the item denominator, so sum only calls.
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
