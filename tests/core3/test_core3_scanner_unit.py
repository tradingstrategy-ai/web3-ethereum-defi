"""Offline unit tests for Core3 scanner project selection."""

import logging
from pathlib import Path

import pytest

from eth_defi.core3 import scanner


def test_scan_projects_filters_requested_slugs_and_logs_missing_slugs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Scan only requested catalogue slugs and report stale mappings.

    The project-list endpoint remains the source of truth. A requested slug
    absent from that list must not result in a detail request, but operators
    need a warning to repair the mapping.

    :param tmp_path:
        Temporary path passed to the mocked database constructor.
    :param monkeypatch:
        Pytest fixture used to isolate Core3 network and database operations.
    :param caplog:
        Pytest fixture used to inspect the mapping diagnostic.
    """

    class FakeDatabase:
        def save(self) -> None:
            pass

        def get_project_count(self) -> int:
            return 1

        def get_snapshot_count(self) -> int:
            return 1

        def get_pol_daily_count(self) -> int:
            return 0

    scanned_slugs: list[str] = []

    def fake_process_project(*args: object, **_: object) -> None:
        scanned_slugs.append(args[2])

    monkeypatch.setattr(scanner, "Core3Database", lambda _: FakeDatabase())
    monkeypatch.setattr(scanner, "fetch_project_list", lambda *_args, **_kwargs: [{"slug": "mapped"}, {"slug": "unmapped"}])
    monkeypatch.setattr(scanner, "_process_project", fake_process_project)
    caplog.set_level(logging.WARNING, logger=scanner.__name__)

    scanner.scan_projects(
        session=object(),
        db_path=tmp_path / "core3.duckdb",
        fetch_pol_history=False,
        fetch_category_history=False,
        fetch_index_pol=False,
        project_slugs={"mapped", "stale"},
        max_workers=1,
    )

    assert scanned_slugs == ["mapped"]
    assert "stale" in caplog.text
