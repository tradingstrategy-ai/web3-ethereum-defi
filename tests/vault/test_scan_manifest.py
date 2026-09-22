"""Tests for the cleaned vault scan manifest producer."""

import datetime
import json
from pathlib import Path

import pandas as pd

from eth_defi.vault.scan_manifest import build_vault_scan_manifest


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
