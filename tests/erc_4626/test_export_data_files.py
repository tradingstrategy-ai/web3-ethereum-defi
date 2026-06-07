"""Test data file export wiring."""

import importlib.util
from pathlib import Path

import pytest


def _load_export_data_files_module():
    """Load ``scripts/erc-4626/export-data-files.py`` as a test module."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts/erc-4626/export-data-files.py"
    spec = importlib.util.spec_from_file_location("export_data_files_test", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core3_duckdb_is_in_data_file_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Core3 DuckDB is part of the R2 data file export list."""
    module = _load_export_data_files_module()
    core3_path = tmp_path / "core3" / "core3.duckdb"
    monkeypatch.setenv("CORE3_DATABASE_PATH", str(core3_path))

    paths = module.get_data_file_paths(tmp_path)

    assert core3_path in paths


def test_missing_core3_duckdb_is_skipped_without_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """Missing Core3 DuckDB follows existing missing-file skip behaviour."""
    module = _load_export_data_files_module()

    uploaded = module.upload_files_to_r2(
        file_paths=[tmp_path / "core3.duckdb"],
        bucket_name="bucket",
        endpoint_url="https://example.invalid",
        access_key_id="access",
        secret_access_key="secret",
    )

    assert uploaded == 0
    assert "File does not exist, skipping" in caplog.text


def test_core3_duckdb_upload_gets_alternative_daily_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Core3 DuckDB uploads to all data buckets and backs up only in alternative bucket."""
    module = _load_export_data_files_module()
    core3_path = tmp_path / "core3" / "core3.duckdb"
    core3_path.parent.mkdir(parents=True)

    for path in module.get_data_file_paths(tmp_path, core3_db_path=core3_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")

    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORE3_DATABASE_PATH", str(core3_path))
    monkeypatch.setenv("R2_DATA_BUCKET_NAME", "public-bucket")
    monkeypatch.setenv("R2_DATA_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_DATA_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_DATA_ENDPOINT_URL", "https://example.invalid")
    monkeypatch.setenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("R2_DAILY_BACKUP", "true")

    uploaded: list[tuple[str, str]] = []
    backups: list[tuple[str, str]] = []

    def fake_upload_file_to_r2(*, bucket_name: str, object_name: str, **_: object) -> bool:
        uploaded.append((bucket_name, object_name))
        return True

    def fake_copy_r2_object_daily_backup(_s3_client: object, bucket_name: str, source_key: str) -> bool:
        backups.append((bucket_name, source_key))
        return True

    monkeypatch.setattr(module, "create_r2_client", lambda **_: object())
    monkeypatch.setattr(module, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(module, "copy_r2_object_daily_backup", fake_copy_r2_object_daily_backup)

    module.main()

    assert ("public-bucket", "core3.duckdb") in uploaded
    assert ("private-bucket", "core3.duckdb") in uploaded
    assert ("private-bucket", "core3.duckdb") in backups
    assert all(bucket == "private-bucket" for bucket, _ in backups)
