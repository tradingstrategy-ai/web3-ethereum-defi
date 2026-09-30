"""Read-only comparison of complete UTC counter windows.

Legacy physical-attempt totals remain authoritative. Operation rows describe
those same requests and must never be added to legacy totals.
"""

import datetime
from pathlib import Path

import duckdb


def fetch_rpc_counter_window(path: Path, start: datetime.date, end: datetime.date) -> dict:
    """Aggregate an explicit half-open window from an immutable snapshot.

    Item counts are deduplicated by chain, phase, date and cycle because each
    method/provider row repeats them. Old counters have no completion outcomes;
    their completed-cycle denominator is deliberately unavailable. Outcome
    counts deduplicate each status within a cycle; a retried cycle may appear
    under both failed and completed. They are not individual attempt counts.
    Windows filter stored UTC cycle-start dates, not individual request times;
    snapshot after the included cycles finish.

    :param path: Existing checkpointed DuckDB snapshot.
    :param start: Inclusive UTC date.
    :param end: Exclusive UTC date.
    :return: Totals, daily rates, groups and available completion diagnostics.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    if end <= start:
        raise ValueError("Counter window end must be after start")
    days = (end - start).days
    with duckdb.connect(str(path), read_only=True) as connection:
        groups = connection.execute("SELECT chain, phase, api_call, rpc_provider_domain, sum(call_count)::BIGINT FROM vault_rpc_api_calls WHERE cycle_started >= ? AND cycle_started < ? AND phase != 'counter_reset' GROUP BY ALL ORDER BY ALL", [start, end]).fetchall()
        phases = connection.execute("SELECT chain, phase, sum(items)::BIGINT, count(*) FROM (SELECT chain, phase, cycle_started, cycle_number, max(items_scanned) AS items FROM vault_rpc_api_calls WHERE cycle_started >= ? AND cycle_started < ? AND phase != 'counter_reset' GROUP BY ALL) GROUP BY ALL ORDER BY ALL", [start, end]).fetchall()
        errors = connection.execute("SELECT coalesce(sum(error_count),0)::BIGINT FROM vault_rpc_api_errors WHERE cycle_started >= ? AND cycle_started < ?", [start, end]).fetchone()[0]
        detail_exists = connection.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='vault_rpc_operation_calls'").fetchone()[0]
        operations, outcomes = [], []
        if detail_exists:
            operations = connection.execute("SELECT chain, phase, operation, api_call, rpc_provider_domain, sum(call_count)::BIGINT FROM vault_rpc_operation_calls WHERE cycle_started >= ? AND cycle_started < ? AND operation != 'outcome' GROUP BY ALL ORDER BY ALL", [start, end]).fetchall()
            outcomes = connection.execute("SELECT chain, phase, outcome, count(*) FROM (SELECT DISTINCT chain, phase, cycle_started, cycle_number, outcome FROM vault_rpc_operation_calls WHERE cycle_started >= ? AND cycle_started < ? AND operation='outcome') GROUP BY ALL ORDER BY ALL", [start, end]).fetchall()
    calls = sum(row[-1] for row in groups)
    return {"start": str(start), "end_exclusive": str(end), "days": days, "calls": calls, "calls_per_day": calls / days, "errors": errors, "groups": groups, "phase_items_and_cycles": phases, "operations": operations, "outcomes": outcomes}


def compare_rpc_counter_windows(before: dict, after: dict) -> list[dict]:
    """Compare physical attempts per day for each chain/phase/method/provider.

    Different durations are normalised, but different chain coverage, cadence
    or incident periods still require operator interpretation.

    :param before: Baseline returned by :func:`fetch_rpc_counter_window`.
    :param after: Follow-up returned by :func:`fetch_rpc_counter_window`.
    :return: Rows with rates and percentage reduction, None for new groups.
    """
    old = {tuple(row[:-1]): row[-1] / before["days"] for row in before["groups"]}
    new = {tuple(row[:-1]): row[-1] / after["days"] for row in after["groups"]}
    return [{"chain": key[0], "phase": key[1], "method": key[2], "provider": key[3], "before_per_day": old.get(key, 0), "after_per_day": new.get(key, 0), "reduction_percent": 100 * (1 - new.get(key, 0) / old[key]) if old.get(key) else None} for key in sorted(old.keys() | new.keys())]
