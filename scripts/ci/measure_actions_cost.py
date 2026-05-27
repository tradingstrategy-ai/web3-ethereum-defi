"""Summarise GitHub Actions run duration JSON.

Reads JSON returned by the GitHub REST API endpoint
``/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs`` from stdin
and prints a small wall-clock runtime summary.

The script intentionally does not call GitHub directly. Keeping network I/O
outside the parser makes the cost comparison reproducible from saved JSON.
"""

from __future__ import annotations

import datetime
import json
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(slots=True, frozen=True)
class RunDuration:
    """Runtime details for one GitHub Actions workflow run.

    :param run_id: GitHub Actions workflow run id.
    :param name: Workflow name.
    :param event: Triggering event name.
    :param conclusion: Run conclusion, e.g. ``success`` or ``failure``.
    :param duration_seconds: Wall-clock runtime in seconds.
    """

    run_id: int
    name: str
    event: str
    conclusion: str | None
    duration_seconds: float


def parse_github_datetime(value: str) -> datetime.datetime:
    """Parse GitHub's ISO-8601 UTC timestamp as a naive UTC datetime.

    GitHub returns timestamps with a trailing ``Z``. The rest of this
    repository uses naive UTC datetimes, so the timezone marker is stripped
    after parsing.

    :param value: Timestamp such as ``2026-05-08T20:08:45Z``.
    :return: Naive UTC datetime.
    """

    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def iter_run_durations(payload: dict[str, Any]) -> Iterable[RunDuration]:
    """Yield completed workflow run durations from a GitHub API payload.

    Runs without ``run_started_at`` or ``updated_at`` are ignored because their
    duration cannot be computed.

    :param payload: Decoded GitHub API JSON response.
    :return: Iterator of parsed run durations.
    """

    for run in payload.get("workflow_runs", []):
        started_at = run.get("run_started_at")
        updated_at = run.get("updated_at")
        if not started_at or not updated_at:
            continue

        started = parse_github_datetime(started_at)
        updated = parse_github_datetime(updated_at)
        duration = (updated - started).total_seconds()
        if duration < 0:
            continue

        yield RunDuration(
            run_id=int(run["id"]),
            name=str(run.get("name", "")),
            event=str(run.get("event", "")),
            conclusion=run.get("conclusion"),
            duration_seconds=duration,
        )


def format_summary(runs: list[RunDuration]) -> str:
    """Format a runtime summary for a list of workflow runs.

    :param runs: Parsed run durations.
    :return: Human-readable summary table.
    """

    if not runs:
        return "No completed workflow runs found."

    durations = [r.duration_seconds for r in runs]
    median_seconds = statistics.median(durations)
    mean_seconds = statistics.fmean(durations)
    longest = max(runs, key=lambda r: r.duration_seconds)

    return "\n".join(
        [
            f"runs: {len(runs)}",
            f"median_wall_clock_seconds: {median_seconds:.1f}",
            f"median_wall_clock_minutes: {median_seconds / 60:.2f}",
            f"mean_wall_clock_minutes: {mean_seconds / 60:.2f}",
            f"max_wall_clock_minutes: {longest.duration_seconds / 60:.2f}",
            f"max_run_id: {longest.run_id}",
            f"max_run_conclusion: {longest.conclusion}",
        ]
    )


def main() -> int:
    """Read GitHub Actions JSON from stdin and print a duration summary.

    :return: Process exit code.
    """

    payload = json.load(sys.stdin)
    runs = list(iter_run_durations(payload))
    print(format_summary(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
