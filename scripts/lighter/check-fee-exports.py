"""Check withdrawal-time fees through live Candle Effect v2 exports.

Fetch public Lighter data, reproduce the legacy gross-equals-net JSON result,
refresh metadata, and verify corrected JSON plus raw and cleaned Parquet.
All outputs and export state stay in a new or empty ``SAMPLE_DIR`` directory
(default ``/tmp/lighter-fee-check``). No data is uploaded.

Run with ``poetry run python scripts/lighter/check-fee-exports.py``. Optional
environment variables are ``SAMPLE_DIR``, ``HTTP_TIMEOUT`` (default 30 seconds)
and ``LOG_LEVEL`` (default info).

Fee semantics follow `Lighter's public-pool documentation
<https://docs.lighter.xyz/trading/public-pools>`__ and the independent
`Cipher Intelligence public-pool example
<https://cipher-intelligence.io/blog/lighter-dex-lp-staking-guide/>`__.
"""

import json
import logging
import math
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.constants import LIGHTER_ETHEREUM
from eth_defi.lighter.daily_metrics import run_daily_scan
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault_data_export import merge_into_uncleaned_parquet, merge_into_vault_database
from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets, process_raw_vault_scan_data
from eth_defi.types import Percent
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.vault.top_vaults_json import main as export_vault_json

logger = logging.getLogger(__name__)

#: Candle Effect v2, an Ethereum Lighter public pool with a 30% operator fee.
ACCOUNT_INDEX = 281474976543116

#: Expected current fee, expressed as a fraction of positive investor profits.
PERFORMANCE_FEE: Percent = 0.3


def _export_sample_json(output_dir: Path, spec: VaultSpec, filename: str) -> dict:
    """Run the normal JSON pipeline using isolated paths and forced metrics.

    Both runs share export state, so refreshing the metadata must replace the
    legacy result even when the exporter would otherwise reuse a cached row.

    :param output_dir: Isolated metadata, price, state and output directory.
    :param spec: Sample vault identity to recalculate.
    :param filename: JSON output filename within the directory.
    :return: The single sample vault read back from the written JSON.
    """
    export_vault_json(
        data_dir=output_dir,
        vault_db_path=output_dir / "vault-metadata-db.pickle",
        parquet_path=output_dir / "cleaned-vault-prices-1h.parquet",
        output_path=output_dir / filename,
        core3_db_path=output_dir / "unused-core3.duckdb",
        xerberus_db_path=output_dir / "unused-xerberus.duckdb",
        feed_db_path=output_dir / "unused-feed.duckdb",
        force_vault_ids={spec.as_string_id()},
    )
    payload = json.loads((output_dir / filename).read_text())
    assert len(payload["vaults"]) == 1, "Sample vault did not qualify for JSON export"
    record = payload["vaults"][0]
    assert record["id"] == spec.as_string_id()
    return record


def main() -> None:  # noqa: PLR0914 - retain source, legacy and corrected evidence for the end-to-end check
    """Collect the live pool and verify both file export pipelines.

    The current fee is applied to historical sample observations consistently
    with the metadata-based return model. Existing cleaning rules exclude
    inactive lead time; the fee change must not alter that selection.

    :return: None; writes sample artifacts and a verification summary.
    """
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    output_dir = Path(os.environ.get("SAMPLE_DIR", str(Path(tempfile.gettempdir()) / "lighter-fee-check"))).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"Sample output directory must be empty: {output_dir}")

    # These explicit overrides take precedence over any sourced production
    # settings. All mutable export and rate-limit state stays in the sample.
    os.environ["VAULT_EXPORT_STATE_PATH"] = str(output_dir / "vault-export-state.json")
    os.environ["VAULT_METRICS_STATE_PATH"] = str(output_dir / "vault-metrics-state.json")
    spec = VaultSpec(LIGHTER_ETHEREUM.chain_id, LIGHTER_ETHEREUM.format_pool_address(ACCOUNT_INDEX))
    metadata_path = output_dir / "vault-metadata-db.pickle"
    raw_path = output_dir / "vault-prices-1h.parquet"
    cleaned_path = output_dir / "cleaned-vault-prices-1h.parquet"

    logger.info("Collecting live Candle Effect v2 into %s", output_dir)
    with create_lighter_session(rate_limit_db_path=output_dir / "rate-limit.sqlite") as session:
        db = run_daily_scan(
            session,
            db_path=output_dir / "lighter-pools.duckdb",
            pool_indices=[ACCOUNT_INDEX],
            max_workers=1,
            timeout=float(os.environ.get("HTTP_TIMEOUT", "30")),
        )
        try:
            assert db.get_pool_count() == 1, "Live scan did not collect the sample pool"
            vault_db = merge_into_vault_database(db, metadata_path)
            fees = vault_db.rows[spec]["_fees"]
            assert fees.performance == PERFORMANCE_FEE, f"Expected a live 30% operator fee, got {fees.performance}"
            raw = merge_into_uncleaned_parquet(db, raw_path)
            source = db.get_pool_daily_prices(ACCOUNT_INDEX)
            restored_raw = pd.read_parquet(raw_path)
            assert len(source) == len(raw) == len(restored_raw)
            np.testing.assert_allclose(restored_raw["performance_fee"].to_numpy(dtype=float), PERFORMANCE_FEE, rtol=1e-7)
            pd.testing.assert_series_equal(
                restored_raw["share_price"].reset_index(drop=True),
                source["share_price"].reset_index(drop=True),
                check_dtype=False,
                check_exact=True,
            )

            logger.info("Cleaning %d source observations with the normal Parquet pipeline", len(raw))
            generate_cleaned_vault_datasets(
                vault_db_path=metadata_path,
                price_df_path=raw_path,
                cleaned_price_df_path=cleaned_path,
                settlement_db_path=output_dir / "unused-settlements.duckdb",
                logger=logger.info,
            )
            cleaned = pd.read_parquet(cleaned_path)
            np.testing.assert_allclose(cleaned["performance_fee"].to_numpy(dtype=float), PERFORMANCE_FEE, rtol=1e-7)
            assert not cleaned.empty
            source_prices = restored_raw.set_index("timestamp")["share_price"]
            pd.testing.assert_series_equal(cleaned["share_price"], source_prices.reindex(cleaned.index), check_dtype=False, check_exact=True)

            # Compare selection against the old zero-fee raw export so normal
            # initialisation/outlier filtering cannot hide fee-induced loss.
            baseline_raw = restored_raw.copy()
            baseline_raw["performance_fee"] = 0.0
            baseline_cleaned = process_raw_vault_scan_data(vault_db, baseline_raw, logger=logger.info)
            pd.testing.assert_index_equal(cleaned.index, baseline_cleaned.index, exact=False)
            assert cleaned["share_price"].tolist() == baseline_cleaned["share_price"].tolist()

            logger.info("Reproducing legacy internalised metadata in the JSON exporter")
            fees.fee_mode = VaultFeeMode.internalised_skimming
            vault_db.write(metadata_path)
            legacy = _export_sample_json(output_dir, spec, "legacy-vaults.json")
            assert legacy["lifetime_return"] == legacy["lifetime_return_net"]

            logger.info("Refreshing metadata and forcing recalculation of the corrected JSON")
            merge_into_vault_database(db, metadata_path)
            corrected = _export_sample_json(output_dir, spec, "top_vaults_by_chain.json")
        finally:
            db.close()

    assert corrected["fee_mode"] == "externalised"
    assert corrected["fee_internalised"] is False
    for key in ("gross_fees", "net_fees"):
        assert corrected[key]["fee_mode"] == "externalised"
        assert corrected[key]["performance"] == PERFORMANCE_FEE
    assert corrected["lifetime_return"] == legacy["lifetime_return"]
    gross = corrected["lifetime_return"]
    expected_net = gross * (1 - PERFORMANCE_FEE) if gross > 0 else gross
    assert math.isclose(corrected["lifetime_return_net"], expected_net, rel_tol=1e-9)
    assert math.isclose(corrected["cagr_net"], (1 + expected_net) ** (1 / corrected["years"]) - 1, rel_tol=1e-9)
    for period in corrected["period_results"]:
        period_gross = period["returns_gross"]
        if period_gross is not None and period["returns_net"] is not None:
            period_net = period_gross * (1 - PERFORMANCE_FEE) if period_gross > 0 else period_gross
            assert math.isclose(period["returns_net"], period_net, rel_tol=1e-9, abs_tol=1e-12), period

    summary = {
        "checked_at": native_datetime_utc_now().isoformat(),
        "provider": LIGHTER_ETHEREUM.api_url,
        "vault_id": spec.as_string_id(),
        "source_rows": len(source),
        "raw_parquet_rows": len(restored_raw),
        "cleaned_parquet_rows": len(cleaned),
        "cleaning_selection_unchanged": True,
        "source_share_prices_unchanged": True,
        "performance_fee": corrected["net_fees"]["performance"],
        "fee_mode": corrected["fee_mode"],
        "gross_return": gross,
        "legacy_net_return": legacy["lifetime_return_net"],
        "corrected_net_return": corrected["lifetime_return_net"],
        "gross_cagr": corrected["cagr"],
        "corrected_net_cagr": corrected["cagr_net"],
    }
    (output_dir / "verification.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(tabulate(summary.items(), headers=["Check", "Result"]))
    logger.info("Live fee export check passed; artifacts retained at %s", output_dir)


if __name__ == "__main__":
    main()
