"""Tests for the cleaned vault scan manifest producer."""

import datetime
import json
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.vault import scan_all_chains, scan_manifest
from eth_defi.vault.scan_all_chains import ChainConfig, ChainResult, run_scan_tick, save_cycle_state
from eth_defi.vault.scan_manifest import _format_utc, _load_price_scan_state, build_vault_scan_manifest


def test_build_manifest_uses_cleaned_snapshot_and_price_state(tmp_path: Path) -> None:
    """Check manifest timestamps come from the uploaded snapshot and price state.

    1. Build a small cleaned parquet with two chain observations.
    2. Write price-only scan provenance, including a skipped chain.
    3. Build the manifest and verify maxima and null semantics.
    """

    # 1. Build a small cleaned parquet with two chain observations.
    parquet_path = tmp_path / "cleaned-vault-prices-1h.parquet"
    pd.DataFrame(
        {
            "chain": [9999, 9999, 1],
            "timestamp": [
                datetime.datetime(2026, 9, 22, 0, 30, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 9, 22, 1, 0, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 9, 21, 23, 0, tzinfo=datetime.timezone.utc),
            ],
        }
    ).to_parquet(parquet_path)

    # 2. Write price-only scan provenance, including a skipped chain.
    state_path = tmp_path / "vault-price-scan-state.json"
    state_path.write_text(
        json.dumps(
            {
                "items": {
                    "9999": "2026-09-22T02:55:00",
                    "143": "2026-09-21T23:10:00",
                }
            }
        )
    )

    # 3. Build the manifest and verify maxima and null semantics.
    manifest = build_vault_scan_manifest(
        cleaned_price_path=parquet_path,
        price_scan_state_path=state_path,
        price_object_key="cleaned-vault-prices-1h.parquet",
        price_etag="etag-1",
        published_at=datetime.datetime(2026, 9, 22, 3, 20, tzinfo=datetime.timezone.utc),
    )
    assert manifest["chains"]["9999"]["last_candle_at"] == "2026-09-22T01:00:00Z"
    assert manifest["chains"]["9999"]["last_successful_price_scan_ended_at"] == "2026-09-22T02:55:00Z"
    assert manifest["chains"]["143"]["last_candle_at"] is None
    assert manifest["chains"]["1"]["last_successful_price_scan_ended_at"] is None
    assert manifest["price_file"] == {"key": "cleaned-vault-prices-1h.parquet", "etag": "etag-1"}


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (datetime.datetime(2026, 9, 22), "2026-09-22T00:00:00Z"),
        (datetime.datetime(2026, 9, 22, microsecond=120000), "2026-09-22T00:00:00.12Z"),
        (datetime.datetime(2026, 9, 22, 2, tzinfo=datetime.timezone(datetime.timedelta(hours=2))), "2026-09-22T00:00:00Z"),
    ],
)
def test_manifest_timestamp_format(timestamp: datetime.datetime, expected: str) -> None:
    """Keep producer timestamps comparable across scanner and Arrow inputs.

    Format naive UTC, fractional seconds and offset-aware observations using
    the same wire convention consumed by the readiness client.

    :param timestamp: Input observation or scan completion time.
    :param expected: UTC wire representation.
    :return: None; asserts the exact JSON timestamp string.
    """
    assert _format_utc(timestamp) == expected


def test_price_provenance_missing_and_invalid(tmp_path: Path) -> None:
    """Distinguish first-run unknown provenance from corrupt scan state.

    Missing state is valid during initial deployment; malformed timestamps
    must fail publication instead of silently disappearing from the receipt.

    :param tmp_path: Isolated scanner state directory.
    :return: None; checks initial state and invalid value handling.
    """
    path = tmp_path / "state.json"
    assert _load_price_scan_state(path) == {}
    path.write_text(json.dumps({"items": {"9999": 123}}))
    with pytest.raises(ValueError, match="Invalid price scan timestamp"):
        _load_price_scan_state(path)
    path.write_text(json.dumps({"items": {"9999": "not-a-timestamp"}}))
    with pytest.raises(ValueError):
        _load_price_scan_state(path)
    path.write_text(json.dumps({"items": {"09999": None}}))
    with pytest.raises(ValueError, match="Invalid chain ID"):
        _load_price_scan_state(path)
    save_cycle_state({"9999": "2026-09-22T00:00:00"}, path)
    assert _load_price_scan_state(path) == {"9999": "2026-09-22T00:00:00Z"}


@pytest.mark.parametrize(("native", "scan_prices", "price_ok", "retry"), [(False, True, True, False), (False, False, True, False), (False, True, None, False), (False, True, False, False), (False, True, True, True), (True, False, True, False), (True, True, False, False)])
def test_scanner_price_provenance_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: bool, scan_prices: bool, price_ok: bool | None, retry: bool) -> None:
    """Keep metadata-only and failed scans out of published price provenance.

    Drive the real tick coordinator with EVM and HyperCore collector results,
    including an EVM retry. Native collection fetches prices independently of
    the EVM ``scan_prices`` switch.

    :param tmp_path: Isolated state paths.
    :param monkeypatch: Replace collectors and dashboard, not the coordinator.
    :param native: Exercise HyperCore rather than the EVM collection path.
    :param scan_prices: EVM price scanning switch.
    :param price_ok: Collector's explicit price-success flag.
    :param retry: Fail the first EVM attempt before succeeding.
    :return: None; asserts exactly which chain advances price provenance.
    """
    name = "Hypercore" if native else "Ethereum"
    results = [ChainResult(name=name, status="success", price_scan_ok=price_ok)]
    if retry:
        results.insert(0, ChainResult(name=name, status="failed"))
    monkeypatch.setattr(scan_all_chains, "scan_chain", lambda *args, **kwargs: results.pop(0))
    monkeypatch.setattr(scan_all_chains, "scan_hypercore_fn", lambda *args, **kwargs: results.pop(0))
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *args, **kwargs: None)
    saved: list[str] = []
    run_scan_tick(
        chains=[] if native else [ChainConfig("Ethereum", "JSON_RPC_ETHEREUM", True)],
        active_protocols=["Hypercore"] if native else [],
        scan_prices=scan_prices,
        scan_hypercore=native,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=int(retry),
        skip_post_processing=True,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vaults.pickle",
        uncleaned_price_path=tmp_path / "raw.parquet",
        reader_state_path=tmp_path / "readers.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-hf.duckdb",
        grvt_db_path=tmp_path / "grvt.duckdb",
        lighter_db_path=tmp_path / "lighter.duckdb",
        hibachi_db_path=tmp_path / "hibachi.duckdb",
        apex_db_path=tmp_path / "apex.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path,
        on_price_scan_success=saved.append,
    )
    assert saved == (["9999" if native else "1"] if price_ok and (native or scan_prices) else [])


def test_publish_manifest_private_object_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the publisher's private object and cache contract without writes.

    Replace only the R2 transport, then build a receipt from real sparse
    parquet rows. This verifies the source HEAD identity and exact upload
    metadata without replacing a production manifest.

    :param tmp_path: Isolated local price snapshot directory.
    :param monkeypatch: Replace environment and R2 transport for this unit test.
    :return: None; asserts the uploaded receipt and storage policy.
    """
    for name, value in {
        "R2_ALTERNATIVE_VAULT_METADATA_BUCKET_NAME": "private-vault-data",
        "R2_DATA_ACCESS_KEY_ID": "unit-test-key",
        "R2_DATA_SECRET_ACCESS_KEY": "unit-test-secret",
        "R2_DATA_ENDPOINT_URL": "https://r2.invalid",
        "UPLOAD_PREFIX": "staging/",
    }.items():
        monkeypatch.setenv(name, value)
    parquet_path = tmp_path / "cleaned-vault-prices-1h.parquet"
    pd.DataFrame({"chain": [9999, 9999], "timestamp": [datetime.datetime(2026, 9, 21, 20), datetime.datetime(2026, 9, 22)]}).to_parquet(parquet_path)
    client = object()
    monkeypatch.setattr(scan_manifest, "create_r2_client", lambda **kwargs: client)

    def fetch_head(actual_client: object, bucket: str, key: str) -> dict[str, str]:
        """Check the exact private object whose version enters the receipt.

        Called by the publisher in place of the network HEAD transport.

        :param actual_client: Transport client created by the publisher.
        :param bucket: Configured private bucket.
        :param key: Prefixed cleaned price key.
        :return: A multipart-style ETag, deliberately not an MD5 checksum.
        """
        assert actual_client is client
        assert bucket == "private-vault-data"
        assert key == "staging/cleaned-vault-prices-1h.parquet"
        return {"ETag": '"opaque-version-3"'}

    uploaded = {}
    monkeypatch.setattr(scan_manifest, "fetch_r2_object_head", fetch_head)
    monkeypatch.setattr(scan_manifest, "upload_bytes_to_r2", lambda **kwargs: uploaded.update(kwargs))
    assert scan_manifest.publish_vault_scan_manifest(parquet_path, tmp_path / "missing-state.json", published_at=datetime.datetime(2026, 9, 22, 1))
    assert uploaded["bucket_name"] == "private-vault-data"
    assert uploaded["object_name"] == "staging/vault-scan-manifest.json"
    assert uploaded["content_type"] == "application/json"
    assert uploaded["cache_control"] == "no-store"
    manifest = json.loads(uploaded["payload"])
    assert manifest["price_file"]["etag"] == "opaque-version-3"
    assert manifest["chains"]["9999"]["last_candle_at"] == "2026-09-22T00:00:00Z"
    assert manifest["chains"]["9999"]["last_successful_price_scan_ended_at"] is None
    with pytest.raises(ValueError, match="strong opaque price ETag"):
        build_vault_scan_manifest(parquet_path, tmp_path / "missing-state.json", "prices.parquet", 'W/"weak-version"', datetime.datetime(2026, 9, 22, 1))
