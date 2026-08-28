"""Inspect the local private crypto-vaults export without network access.

The report reads the exported metadata and daily price Parquet, checks their
basic identities and prints ETH- and BTC-denominated vault name, protocol,
denomination token, native and USD lifetime CAGR, and current TVL. The USD TVL
figure uses the latest local currency API ETH/BTC rate and treats each wrapper
as its canonical underlying. It is a comparison estimate, not a wrapper
redemption valuation.

Environment variables:

- ``PIPELINE_DATA_DIR``: Common vault data directory.
- ``CRYPTO_VAULTS_DIRECTORY``: Local private bundle directory. Defaults to
  ``crypto-vaults`` below the pipeline data directory.
- ``CURRENCY_API_DB_PATH`` / ``CURRENCY_API_DATABASE_PATH``: Exchange-rate
  DuckDB path. Defaults to ``exchange-rates.duckdb`` below the pipeline data
  directory.
- ``LIMIT``: Optional maximum rows after sorting. Zero means all rows.
"""

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from tabulate import tabulate

from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.research.vault_metrics import MAX_VALID_NAV
from eth_defi.vault.crypto_vaults import CRYPTO_VAULTS_BUNDLE_NAME, CryptoVaultPaths, resolve_crypto_vault_paths
from eth_defi.vault.top_vaults_json import is_blacklisted_record
from eth_defi.vault.vaultdb import get_pipeline_data_dir


def _get_lifetime_cagr(vault: dict[str, Any]) -> float | None:
    """Choose the net lifetime CAGR when the export contains one.

    :param vault:
        One JSON vault-metadata record.
    :return:
        Net lifetime CAGR, gross lifetime CAGR or ``None``.
    """
    return vault.get("cagr_net") if vault.get("cagr_net") is not None else vault.get("cagr")


def _get_lifetime_usd_cagr(vault: dict[str, Any]) -> float | None:
    """Choose the net USD lifetime CAGR from converted period metrics.

    The crypto export keeps its established native CAGR fields untouched. This
    value comes from the optional USD period view, which applies the rate
    snapshot pinned in the metadata document.

    :param vault:
        One JSON ETH/BTC vault-metadata record.
    :return:
        Net USD lifetime CAGR, gross USD CAGR or ``None`` when unavailable.
    """
    usd_metrics = vault.get("periodic_metrics_usd")
    if usd_metrics is None:
        return None
    lifetime = next((metric for metric in usd_metrics if metric.get("period") == "lifetime"), None)
    if lifetime is None or lifetime.get("error_reason") is not None:
        return None
    cagr = lifetime.get("cagr_net") if lifetime.get("cagr_net") is not None else lifetime.get("cagr_gross")
    if cagr is None:
        message = f"USD lifetime metric is missing CAGR fields for {vault['id']}"
        raise ValueError(message)
    return cagr


def _get_lifetime_usd_start(vault: dict[str, Any]) -> str | None:
    """Return the effective USD lifetime sample start for comparison.

    :param vault:
        One JSON ETH/BTC vault-metadata record.
    :return:
        ISO timestamp for the USD lifetime sample start, or ``None``.
    """
    usd_metrics = vault.get("periodic_metrics_usd")
    if usd_metrics is None:
        return None
    lifetime = next((metric for metric in usd_metrics if metric.get("period") == "lifetime"), None)
    if lifetime is None or lifetime.get("error_reason") is not None:
        return None
    value = lifetime.get("samples_start_at")
    return str(value) if value is not None else None


def _format_tvl(vault: dict[str, Any]) -> str:
    """Format native-unit current TVL for operator inspection.

    :param vault:
        One JSON vault-metadata record.
    :return:
        Human-readable total assets with its denomination symbol.
    """
    total_assets = vault.get("current_total_assets")
    unit = vault.get("denomination") or "unknown"
    return "N/A" if total_assets is None else f"{float(total_assets):,.6g} {unit}"


def _load_latest_usd_rates(path: Path) -> dict[str, tuple[float, str]]:
    """Read latest USD-per-ETH and USD-per-BTC rates from local DuckDB.

    Currency API stores quote units per USD, so the stored USD→ETH/BTC value
    is inverted for the displayed USD equivalent. The database is opened
    read-only because examination must never change scanner state.

    :param path:
        Currency API exchange-rate DuckDB file.
    :return:
        USD per native ETH/BTC unit and each rate's as-of date.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT quote_currency, rate, date
            FROM (
                SELECT quote_currency, rate, date,
                    ROW_NUMBER() OVER (PARTITION BY quote_currency ORDER BY date DESC) AS row_number
                FROM exchange_rates
                WHERE base_currency = 'usd'
                    AND source = ?
                    AND quote_currency IN ('btc', 'eth')
            )
            WHERE row_number = 1
            """,
            [SOURCE_NAME],
        ).fetchall()
    finally:
        connection.close()
    rates = {str(currency).upper(): (1 / float(rate), str(date)) for currency, rate, date in rows if rate > 0}
    missing = {"BTC", "ETH"}.difference(rates)
    if missing:
        raise ValueError(f"Currency API database lacks latest USD rates for: {sorted(missing)!r}")
    return rates


def build_performance_table(metadata: dict[str, Any], usd_rates: dict[str, tuple[float, str]]) -> pd.DataFrame:
    """Convert crypto export metadata to a compact operator report.

    :param metadata:
        Parsed ``crypto-vault-metadata.json`` document.
    :param usd_rates:
        Current USD-per-native-token values from the currency API database.
    :return:
        Table sorted by approximate USD-equivalent TVL.
    """
    rows = []
    for vault in metadata["vaults"]:
        usd_rate, rate_date = usd_rates[vault["canonical_underlying"]]
        rows.append(
            {
                "Vault name": vault.get("name") or vault.get("symbol") or vault["id"],
                "Protocol": vault.get("protocol") or "Unknown",
                "Denomination token": vault.get("denomination") or "Unknown",
                "Lifetime CAGR (base)": _get_lifetime_cagr(vault),
                "Lifetime CAGR (USD)": _get_lifetime_usd_cagr(vault),
                "USD from": _get_lifetime_usd_start(vault),
                "TVL": _format_tvl(vault),
                "Approx. TVL (USD)": float(vault.get("current_total_assets") or 0) * usd_rate,
                "USD rate as of": rate_date,
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        return table.reindex(columns=["Vault name", "Protocol", "Denomination token", "Lifetime CAGR (base)", "Lifetime CAGR (USD)", "USD from", "TVL", "Approx. TVL (USD)", "USD rate as of"])
    return table.sort_values(["Approx. TVL (USD)", "Vault name"], ascending=[False, True], kind="stable")


def _validate_export(paths: CryptoVaultPaths, metadata: dict[str, Any]) -> None:
    """Check that local metadata and daily observations describe one bundle.

    :param paths:
        Local crypto bundle paths.
    :param metadata:
        Parsed metadata document.
    :return:
        ``None``. Raises for incomplete or inconsistent local artefacts.
    """
    if metadata.get("bundle") != CRYPTO_VAULTS_BUNDLE_NAME:
        raise ValueError(f"Unexpected crypto metadata bundle: {metadata.get('bundle')!r}")
    prices = pd.read_parquet(paths.cleaned_price_path, columns=["id"])
    price_ids = set(prices["id"].astype(str))
    metadata_ids = {str(vault["id"]) for vault in metadata["vaults"]}
    missing_price_ids = metadata_ids - price_ids
    if missing_price_ids:
        raise ValueError(f"Crypto metadata has vaults absent from daily prices: {sorted(missing_price_ids)!r}")
    if paths.manifest_path.exists():
        manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        if manifest.get("vault_count_total") != len(metadata_ids):
            message = "Crypto manifest vault count disagrees with metadata"
            raise ValueError(message)
        if manifest.get("price_row_count_total") != len(prices):
            message = "Crypto manifest price-row count disagrees with Parquet"
            raise ValueError(message)


def main() -> None:
    """Validate and print the local private crypto-vaults performance table.

    :return:
        ``None`` after printing the requested report.
    """
    data_dir = get_pipeline_data_dir()
    crypto_directory = os.environ.get("CRYPTO_VAULTS_DIRECTORY")
    paths = resolve_crypto_vault_paths(data_dir, Path(crypto_directory).expanduser() if crypto_directory else None)
    currency_db_path = Path(os.environ.get("CURRENCY_API_DB_PATH") or os.environ.get("CURRENCY_API_DATABASE_PATH") or data_dir / "exchange-rates.duckdb").expanduser()
    limit = int(os.environ.get("LIMIT", "0"))
    if limit < 0:
        raise ValueError(f"LIMIT must be non-negative, got {limit}")
    for path in (paths.cleaned_price_path, paths.metadata_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    metadata = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
    _validate_export(paths, metadata)
    non_stablecoin_vaults = [vault for vault in metadata["vaults"] if vault.get("denomination_family") != "stablecoin"]
    stablecoin_count = len(metadata["vaults"]) - len(non_stablecoin_vaults)
    if stablecoin_count:
        print(f"Excluding {stablecoin_count:,} stablecoin-denominated vault(s) from the report.")
    report_vaults = [vault for vault in non_stablecoin_vaults if not is_blacklisted_record(vault)]
    blacklisted_count = len(non_stablecoin_vaults) - len(report_vaults)
    if blacklisted_count:
        print(f"Excluding {blacklisted_count:,} blacklisted vault(s) from the report.")
    table = build_performance_table({**metadata, "vaults": report_vaults}, _load_latest_usd_rates(currency_db_path))
    abnormal_tvl = table["Approx. TVL (USD)"] > MAX_VALID_NAV
    abnormal_tvl_count = int(abnormal_tvl.sum())
    if abnormal_tvl_count:
        print(f"Excluding {abnormal_tvl_count:,} vault(s) above ${MAX_VALID_NAV:,.0f} from the report as abnormal scanner TVL values.")
        table = table.loc[~abnormal_tvl]
    if limit:
        table = table.head(limit)
    display_table = table.copy()
    for column in ("Lifetime CAGR (base)", "Lifetime CAGR (USD)"):
        display_table[column] = display_table[column].map(lambda value: "N/A" if pd.isna(value) else f"{value:.2%}")
    display_table["USD from"] = display_table["USD from"].fillna("N/A")
    display_table["Approx. TVL (USD)"] = display_table["Approx. TVL (USD)"].map(lambda value: f"${value:,.0f}")
    print(tabulate(display_table, headers="keys", tablefmt="rounded_outline", showindex=False))


if __name__ == "__main__":
    main()
