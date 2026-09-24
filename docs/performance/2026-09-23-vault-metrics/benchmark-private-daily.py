"""Compare the complete private stablecoin metadata output on a local sample.

Set ``CRYPTO_BENCHMARK_PRICE_DATABASE`` and ``CRYPTO_BENCHMARK_VAULT_DATABASE``
to retained production-format files. The script writes only to a temporary
directory and records its result beside this file.
"""

import datetime
import hashlib
import os
import tempfile
import time
from pathlib import Path

import orjson
import pandas as pd

from eth_defi.research import vault_metrics
from eth_defi.vault import crypto_vaults
from eth_defi.vault.crypto_vaults import DenominationFamily, _family_by_vault_id, build_crypto_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase


def main() -> None:
    """Build the same sample with both daily preparation implementations.

    The baseline restores the old full-column resampling call in memory. Both
    runs use isolated freshness state and a fixed clock; the saved JSON files
    must match byte for byte.

    :return: ``None`` after writing the comparison artefact.
    """
    source_path = Path(os.environ["CRYPTO_BENCHMARK_PRICE_DATABASE"]).expanduser()
    vault_db_path = Path(os.environ["CRYPTO_BENCHMARK_VAULT_DATABASE"]).expanduser()
    vault_db = VaultDatabase.read(vault_db_path)
    family_by_id = _family_by_vault_id(vault_db)
    prices = pd.read_parquet(source_path)
    prices["id"] = prices["id"].astype(str)
    selected_ids = [vault_id for vault_id in prices["id"].drop_duplicates() if family_by_id.get(vault_id) is DenominationFamily.stablecoin][:500]
    prices = prices.loc[prices["id"].isin(selected_ids)]
    fixed_now = datetime.datetime(2026, 9, 23, 12, 0, 0)

    old_helper = crypto_vaults.calculate_sparse_daily_returns_for_all_vaults
    old_excluded = crypto_vaults.UNUSED_METRIC_PRICE_COLUMNS
    old_crypto_clock = crypto_vaults.native_datetime_utc_now
    old_metric_clock = vault_metrics.native_datetime_utc_now
    result: dict[str, object] = {}
    try:
        crypto_vaults.native_datetime_utc_now = lambda: fixed_now
        vault_metrics.native_datetime_utc_now = lambda: fixed_now
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            sample_path = root / "sample.parquet"
            prices.to_parquet(sample_path)
            outputs: dict[str, bytes] = {}
            for variant in ("baseline", "candidate"):
                variant_dir = root / variant
                variant_dir.mkdir()
                if variant == "baseline":
                    crypto_vaults.calculate_sparse_daily_returns_for_all_vaults = vault_metrics.calculate_hourly_returns_for_all_vaults
                    crypto_vaults.UNUSED_METRIC_PRICE_COLUMNS = frozenset()
                else:
                    crypto_vaults.calculate_sparse_daily_returns_for_all_vaults = old_helper
                    crypto_vaults.UNUSED_METRIC_PRICE_COLUMNS = old_excluded
                output_path = variant_dir / "crypto-vault-metadata.json"
                started_at = time.perf_counter()
                metadata = build_crypto_vault_metadata(
                    vault_db_path=vault_db_path,
                    cleaned_price_path=sample_path,
                    metadata_path=output_path,
                    sticky_state_path=variant_dir / "crypto-vault-export-state.json",
                )
                outputs[variant] = output_path.read_bytes()
                result[f"{variant}_seconds"] = round(time.perf_counter() - started_at, 3)
                result[f"{variant}_records"] = len(metadata["vaults"])
                print(f"{variant}: {result[f'{variant}_records']} records in {result[f'{variant}_seconds']}s", flush=True)

            result["metadata_json_byte_equal"] = outputs["baseline"] == outputs["candidate"]
            result["sticky_state_byte_equal"] = (root / "baseline/crypto-vault-export-state.json").read_bytes() == (root / "candidate/crypto-vault-export-state.json").read_bytes()
            result["metrics_state_byte_equal"] = (root / "baseline/crypto-vault-metrics-state.json").read_bytes() == (root / "candidate/crypto-vault-metrics-state.json").read_bytes()
    finally:
        crypto_vaults.calculate_sparse_daily_returns_for_all_vaults = old_helper
        crypto_vaults.UNUSED_METRIC_PRICE_COLUMNS = old_excluded
        crypto_vaults.native_datetime_utc_now = old_crypto_clock
        vault_metrics.native_datetime_utc_now = old_metric_clock

    with source_path.open("rb") as source_file, vault_db_path.open("rb") as vault_db_file:
        source_sha256 = hashlib.file_digest(source_file, "sha256").hexdigest()
        vault_db_sha256 = hashlib.file_digest(vault_db_file, "sha256").hexdigest()
    result.update(
        source_sha256=source_sha256,
        vault_db_sha256=vault_db_sha256,
        sampled_ids_sha256=hashlib.sha256("\n".join(selected_ids).encode()).hexdigest(),
        selected_vaults=len(selected_ids),
        source_rows=len(prices),
        fixed_now=fixed_now.isoformat(),
    )
    destination = Path(__file__).with_name("private-daily-publication-parity.json")
    destination.write_bytes(orjson.dumps(result, option=orjson.OPT_INDENT_2))
    print(f"Saved {destination}: metadata byte equal={result['metadata_json_byte_equal']}", flush=True)
    assert result["metadata_json_byte_equal"] and result["sticky_state_byte_equal"] and result["metrics_state_byte_equal"]


if __name__ == "__main__":
    main()
