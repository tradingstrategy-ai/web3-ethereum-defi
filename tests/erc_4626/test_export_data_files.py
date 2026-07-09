"""Test data file export wiring."""

from pathlib import Path

import pytest

from eth_defi.vault import data_file_export
from eth_defi.vault.settlement_data import VAULT_SETTLEMENT_DATABASE_FILENAME, VaultSettlementDatabase


def write_export_test_file(path: Path) -> None:
    """Create a valid export fixture file.

    Most export files can be byte placeholders because upload tests mock the
    R2 calls. The settlement database is checkpointed before export, so it must
    be a valid DuckDB file instead of arbitrary bytes.

    :param path:
        File path to create.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == VAULT_SETTLEMENT_DATABASE_FILENAME:
        db = VaultSettlementDatabase(path)
        try:
            db.save()
        finally:
            db.close()
    else:
        path.write_bytes(b"data")


def test_core3_duckdb_is_in_data_file_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Core3 DuckDB is part of the R2 data file export list."""
    core3_path = tmp_path / "core3" / "core3.duckdb"
    monkeypatch.setenv("CORE3_DATABASE_PATH", str(core3_path))

    paths = data_file_export.get_data_file_paths(tmp_path)

    assert core3_path in paths


def test_exchange_rate_duckdb_is_in_data_file_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exchange-rate DuckDB is part of the R2 data file export list."""
    exchange_rate_path = tmp_path / "exchange" / "exchange-rates.duckdb"
    monkeypatch.setenv("CURRENCY_API_DB_PATH", str(exchange_rate_path))

    paths = data_file_export.get_data_file_paths(tmp_path)

    assert exchange_rate_path in paths


def test_exchange_rate_duckdb_defaults_to_pipeline_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exchange-rate DuckDB defaults to the active pipeline data directory."""
    monkeypatch.delenv("CURRENCY_API_DB_PATH", raising=False)
    monkeypatch.delenv("CURRENCY_API_DATABASE_PATH", raising=False)

    paths = data_file_export.get_data_file_paths(tmp_path)

    assert tmp_path / "exchange-rates.duckdb" in paths


def test_exchange_rate_duckdb_path_accepts_currency_api_database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exchange-rate DuckDB upload follows the currency API scanner path contract."""
    exchange_rate_path = tmp_path / "rates.duckdb"
    monkeypatch.delenv("CURRENCY_API_DB_PATH", raising=False)
    monkeypatch.setenv("CURRENCY_API_DATABASE_PATH", str(exchange_rate_path))

    paths = data_file_export.get_data_file_paths(tmp_path)

    assert exchange_rate_path in paths


def test_sticky_export_state_files_are_in_data_file_paths(tmp_path: Path):
    """Sticky export state files are part of the R2 data file export list.

    Steps:

    1. Create the sticky export state file.
    2. Create an unrelated JSON file in the same directory.
    3. Build the R2 data file path list.
    4. Assert sticky state file is included and unrelated JSON is ignored.
    """
    # 1. Create the sticky export state file.
    sticky_state = tmp_path / "vault-export-state.json"
    sticky_state.write_text("{}", encoding="utf-8")

    # 2. Create an unrelated JSON file in the same directory.
    unrelated_json = tmp_path / "top_vaults_by_chain.json"
    unrelated_json.write_text("{}", encoding="utf-8")

    # 3. Build the R2 data file path list.
    paths = data_file_export.get_data_file_paths(tmp_path)

    # 4. Assert sticky state file is included and unrelated JSON is ignored.
    assert sticky_state in paths
    assert unrelated_json not in paths


def test_missing_core3_duckdb_is_skipped_without_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """Missing Core3 DuckDB follows existing missing-file skip behaviour."""
    uploaded = data_file_export.upload_files_to_r2(
        file_paths=[tmp_path / "core3.duckdb"],
        bucket_name="bucket",
        endpoint_url="https://example.invalid",
        access_key_id="access",
        secret_access_key="secret",
    )

    assert uploaded == 0
    assert "File does not exist, skipping" in caplog.text


def test_core3_duckdb_upload_gets_alternative_daily_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Core3 DuckDB uploads to all data buckets and backs up only in alternative bucket.

    Steps:

    1. Create all data export files, including the Core3 DuckDB.
    2. Mock R2 upload and daily backup calls.
    3. Assert Core3 uploads to both buckets but daily backup only runs in the
       alternative bucket.
    """
    core3_path = tmp_path / "core3" / "core3.duckdb"
    core3_path.parent.mkdir(parents=True)

    for path in data_file_export.get_data_file_paths(tmp_path, core3_db_path=core3_path):
        write_export_test_file(path)

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

    monkeypatch.setattr(data_file_export, "create_r2_client", lambda **_: object())
    monkeypatch.setattr(data_file_export, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(data_file_export, "copy_r2_object_daily_backup", fake_copy_r2_object_daily_backup)

    data_file_export.main()
    capsys.readouterr()

    assert ("public-bucket", "core3.duckdb") in uploaded
    assert ("private-bucket", "core3.duckdb") in uploaded
    assert ("private-bucket", "core3.duckdb") in backups
    assert all(bucket == "private-bucket" for bucket, _ in backups)


def test_exchange_rate_duckdb_upload_gets_alternative_daily_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """Exchange-rate DuckDB uploads to all data buckets and backs up only in alternative bucket.

    Steps:

    1. Create all data export files, including the exchange-rate DuckDB.
    2. Mock R2 upload and daily backup calls.
    3. Assert exchange rates upload to both buckets but daily backup only
       runs in the alternative bucket.
    """
    core3_path = tmp_path / "core3" / "core3.duckdb"
    exchange_rate_path = tmp_path / "exchange" / "exchange-rates.duckdb"

    for path in data_file_export.get_data_file_paths(
        tmp_path,
        core3_db_path=core3_path,
        exchange_rate_db_path=exchange_rate_path,
    ):
        write_export_test_file(path)

    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORE3_DATABASE_PATH", str(core3_path))
    monkeypatch.setenv("CURRENCY_API_DB_PATH", str(exchange_rate_path))
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

    monkeypatch.setattr(data_file_export, "create_r2_client", lambda **_: object())
    monkeypatch.setattr(data_file_export, "upload_file_to_r2", fake_upload_file_to_r2)
    monkeypatch.setattr(data_file_export, "copy_r2_object_daily_backup", fake_copy_r2_object_daily_backup)

    data_file_export.main()
    capsys.readouterr()

    assert ("public-bucket", "exchange-rates.duckdb") in uploaded
    assert ("private-bucket", "exchange-rates.duckdb") in uploaded
    assert ("private-bucket", "exchange-rates.duckdb") in backups
    assert all(bucket == "private-bucket" for bucket, _ in backups)
