"""Production report downloads must refresh daily and fail closed."""

import datetime
import io
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.vault_report import data as report_data


def make_streaming_body(payload: bytes) -> Mock:
    """Supply a closable byte stream without requiring the optional R2 SDK.

    CI runs these cache tests without the Cloudflare extra; the real R2
    integration test separately exercises boto3's response body.

    :param payload:
        Bytes supplied by the simulated production object.
    :return:
        Body exposing the S3 streaming and close methods.
    """
    stream = io.BytesIO(payload)
    body = Mock()
    body.iter_chunks.side_effect = lambda chunk_size: iter(lambda: stream.read(chunk_size), b"")
    body.close.side_effect = stream.close
    return body


@pytest.fixture
def r2_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a private test bucket without using local credentials.

    Clears preferred and fallback credentials so tests exercise the same
    configuration regardless of the operator's secrets file.

    :param monkeypatch:
        Environment isolation fixture.
    """
    for name in (
        "R2_DATA_ENDPOINT_URL",
        "R2_DATA_ACCESS_KEY_ID",
        "R2_DATA_SECRET_ACCESS_KEY",
        "R2_VAULT_METADATA_ENDPOINT_URL",
        "R2_VAULT_METADATA_ACCESS_KEY_ID",
        "R2_VAULT_METADATA_SECRET_ACCESS_KEY",
        "UPLOAD_PREFIX",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", "private-production")
    monkeypatch.setenv("R2_VAULT_METADATA_ENDPOINT_URL", "https://example.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_VAULT_METADATA_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("R2_VAULT_METADATA_SECRET_ACCESS_KEY", "test-secret")


@pytest.mark.parametrize("age_hours, refreshed", [(23, False), (24, True), (25, True), (-1, True)])
def test_r2_report_cache_expires_daily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, age_hours: int, refreshed: bool) -> None:
    """Refresh at the one-day boundary and reject future cache timestamps.

    An old file must never be selected just because it exists. Fresh downloads
    are reusable throughout the day; the remote modification time is unrelated.

    :param tmp_path:
        Isolated download directory.
    :param monkeypatch:
        Clock isolation fixture.
    :param age_hours:
        Cache age relative to the fixed clock.
    :param refreshed:
        Whether a production download is expected.
    """
    now = datetime.datetime(2026, 10, 1, 12)
    monkeypatch.setattr(report_data, "native_datetime_utc_now", lambda: now)
    path = tmp_path / "prices.parquet"
    path.write_bytes(b"old")
    timestamp = (now - datetime.timedelta(hours=age_hours)).replace(tzinfo=datetime.timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))
    client = Mock()
    client.get_object.return_value = {"Body": make_streaming_body(b"new"), "ContentLength": 3}

    assert report_data.fetch_r2_report_file(client, "private", "prices.parquet", path) == path
    assert path.read_bytes() == (b"new" if refreshed else b"old")
    assert client.get_object.call_count == int(refreshed)


def test_r2_report_failed_refresh_preserves_cache(tmp_path: Path) -> None:
    """A truncated refresh aborts instead of generating charts from stale data.

    The old cache remains intact for diagnosis, and the partial download is
    removed so it cannot be mistaken for a complete production snapshot.

    :param tmp_path:
        Isolated download directory.
    """
    path = tmp_path / "prices.parquet"
    path.write_bytes(b"old")
    body = make_streaming_body(b"partial")
    client = Mock()
    client.get_object.return_value = {"Body": body, "ContentLength": 100}

    with pytest.raises(RuntimeError, match="Incomplete production download"):
        report_data.fetch_r2_report_file(client, "private", "prices.parquet", path, max_age=datetime.timedelta())
    assert path.read_bytes() == b"old"
    assert not path.with_suffix(".parquet.tmp").exists()
    body.close.assert_called_once()


def test_r2_report_inputs_are_isolated_by_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r2_configuration: None) -> None:
    """Download both inputs privately and never reuse another source's cache.

    Legacy HTTP caches, another bucket and a test object prefix must not supply
    production charts, even when their files are younger than one day.

    :param tmp_path:
        Isolated download directory.
    :param monkeypatch:
        Client and loader isolation fixture.
    :param r2_configuration:
        Private bucket and fallback credential fixture.
    """
    (tmp_path / "top_vaults_by_chain.json").write_bytes(b"old public metrics")
    client = Mock()
    client.get_object.side_effect = lambda **kwargs: {"Body": make_streaming_body(b"production"), "ContentLength": 10}
    create_client = Mock(return_value=client)
    loader = Mock()
    monkeypatch.setattr(report_data, "create_r2_client", create_client)
    monkeypatch.setattr(report_data, "fetch_vault_report_data", loader)

    report_data.fetch_vault_report_data_from_r2(tmp_path)
    first_paths = loader.call_args.kwargs
    create_client.assert_called_with(endpoint_url="https://example.r2.cloudflarestorage.com", access_key_id="test-id", secret_access_key="test-secret")
    assert [call.kwargs["Key"] for call in client.get_object.call_args_list] == ["top_vaults_by_chain.json", "cleaned-vault-prices-1h.parquet"]
    assert all(call.kwargs["Bucket"] == "private-production" for call in client.get_object.call_args_list)
    assert all(path.read_bytes() == b"production" for path in first_paths.values())

    report_data.fetch_vault_report_data_from_r2(tmp_path)
    assert client.get_object.call_count == 2
    monkeypatch.setenv("UPLOAD_PREFIX", "test-")
    report_data.fetch_vault_report_data_from_r2(tmp_path)
    assert client.get_object.call_count == 4
    assert client.get_object.call_args.kwargs["Key"] == "test-cleaned-vault-prices-1h.parquet"
    assert loader.call_args.kwargs["prices_path"] != first_paths["prices_path"]
    monkeypatch.setenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME", "another-private-bucket")
    report_data.fetch_vault_report_data_from_r2(tmp_path)
    assert client.get_object.call_count == 6
    assert client.get_object.call_args.kwargs["Bucket"] == "another-private-bucket"


def test_r2_report_requires_private_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r2_configuration: None) -> None:
    """Missing production configuration fails before contacting another source.

    The generator must not silently fall back to public data or a local
    scanner file when its private bucket is unavailable.

    :param tmp_path:
        Isolated download directory.
    :param monkeypatch:
        Environment and client isolation fixture.
    :param r2_configuration:
        Private bucket and fallback credential fixture.
    """
    monkeypatch.delenv("R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME")
    create_client = Mock()
    monkeypatch.setattr(report_data, "create_r2_client", create_client)
    with pytest.raises(RuntimeError, match="R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME"):
        report_data.fetch_vault_report_data_from_r2(tmp_path)
    create_client.assert_not_called()
