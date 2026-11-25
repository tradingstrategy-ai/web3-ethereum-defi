"""Run the manual Pear backfill and print lifetime vault metrics.

Run from the repository root with the HyperEVM RPC configured:

    source .local-test.env
    PYTHONPATH="$PWD" poetry run python scripts/erc-4626/examine-pear-vault-performance.py

The script uses a temporary catalogue and price file. Existing pipeline
observations are read from PIPELINE_DATA_DIR when available. The report runs
the metadata and price migrations in scratch storage, then requests a head
sample for each vault. Launch dates come from the first standard Deposit
event. TVL comes from the current metadata read. CAGR and Sharpe use the
observed USDC share-price series.
"""

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pyarrow.parquet as pq
from tabulate import tabulate

from eth_defi.chain import fetch_block_timestamp
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import PotentialVaultMatch
from eth_defi.erc_4626.vault_protocol.pear.constants import PEAR_CHAIN_ID, PEAR_FIRST_DEPOSIT_BLOCKS
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.research.vault_metrics import calculate_period_metrics, prepare_daily_share_price_series
from eth_defi.research.wrangle_vault_prices import filter_verified_hyperevm_prices
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import VaultDatabase, get_pipeline_data_dir
from eth_defi.vault_protocols.migration import discover_migrations, run_pending_migrations
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME

logger = logging.getLogger(__name__)


def format_performance_table(database: VaultDatabase, prices: pd.DataFrame) -> str:
    """Format lifetime metrics using verified observations from the scratch catalogue.

    Older raw files may include Pear archive calls with unreliable HyperCore
    values. Migration 098 preserves those rows for inspection. The report uses
    the API and onchain head samples with established provenance.

    :param database: Catalogue supplying launch dates, onchain TVL and vault fees.
    :param prices: Raw rows for the four HyperEVM vaults with chain, address, block_number,
        UTC timestamp, share_price, total_assets and vault_poll_frequency columns.
    :return: Markdown table with gross lifetime CAGR and annualised daily Sharpe.
    """
    prices = filter_verified_hyperevm_prices(prices)
    table = []
    for spec, row in sorted(database.rows.items(), key=lambda item: item[1]["NAV"], reverse=True):
        history = prices.loc[(prices["address"] == spec.vault_address) & prices["share_price"].between(0, float("inf"), inclusive="neither")].sort_values(["timestamp", "block_number"]).set_index("timestamp")
        cagr = sharpe = None
        if not history.empty and history.index[0] < row["First seen"] + pd.Timedelta(days=1):
            share_price = history["share_price"]
            daily, returns = prepare_daily_share_price_series(share_price)
            fees = row["_fees"]
            lifetime = calculate_period_metrics("lifetime", fees, fees.get_net_fees(vault_protocol_name="Pear"), share_price, daily, returns, history["total_assets"], pd.Timestamp(share_price.index[-1]))
            if not lifetime.error_reason:
                cagr = lifetime.cagr_gross
                sharpe = lifetime.sharpe
        if cagr is None or sharpe is None:
            logger.info("%s has insufficient launch-to-date observations for one or both lifetime metrics", row["Name"])
        table.append((row["Name"], row["First seen"].date(), f"${row['NAV']:,.2f}", f"{cagr:.2%}" if cagr is not None else "—", f"{sharpe:.2f}" if sharpe is not None else "—"))
    return tabulate(table, headers=("Name", "Launch date (UTC)", "TVL (USDC)", "Gross lifetime CAGR", "Lifetime Sharpe"), tablefmt="github")


def main() -> None:
    """Run the metadata and history migrations, sample current prices and tabulate Pear."""
    setup_console_logging(default_log_level="INFO")
    source = get_pipeline_data_dir()
    rpc_url = read_json_rpc_url(PEAR_CHAIN_ID)
    web3 = create_multi_provider_web3(rpc_url)
    with TemporaryDirectory(prefix="pear-performance-") as temporary:
        scratch = Path(temporary)
        migration_scripts = scratch / "pear-migrations"
        migration_scripts.mkdir()
        for migration in discover_migrations():
            if migration.name in {"096-pear-vault-metadata.py", "098-pear-offchain-history.py"}:
                (migration_scripts / migration.name).write_bytes(migration.source)
        database_path = scratch / "vault-metadata-db.pickle"
        database = VaultDatabase()
        for address, block in PEAR_FIRST_DEPOSIT_BLOCKS.items():
            spec = VaultSpec(PEAR_CHAIN_ID, address)
            launch = fetch_block_timestamp(web3, block)
            database.leads[spec] = PotentialVaultMatch(PEAR_CHAIN_ID, spec.vault_address, block, launch, deposit_count=1)
        database.write(database_path)
        # Legacy database overrides must not point the scratch runner elsewhere.
        os.environ.pop("VAULT_DB", None)
        os.environ.pop("VAULT_DB_PATH", None)

        price_path = scratch / PRICE_FILE_NAME
        source_price_path = source / PRICE_FILE_NAME
        if source_price_path.is_file():
            prices = pq.read_table(source_price_path, filters=[("chain", "==", PEAR_CHAIN_ID), ("address", "in", list(PEAR_FIRST_DEPOSIT_BLOCKS))])
        else:
            prices = VaultHistoricalRead.to_pyarrow_schema().empty_table()
        pq.write_table(prices, price_path)
        run_pending_migrations(dry_run=False, data_dir=scratch, scripts_dir=migration_scripts)
        database = VaultDatabase.read(database_path)

        vaults = []
        token_cache = TokenDiskCache()
        for spec, row in database.rows.items():
            vault = create_vault_instance(web3, spec.vault_address, features={ERC4626Feature.pear_like}, token_cache=token_cache)
            vault.first_seen_at_block = row["_detection_data"].first_seen_at_block
            vaults.append(vault)
        scan_historical_prices_to_parquet(
            price_path,
            web3,
            MultiProviderWeb3Factory(rpc_url, expected_chain_id=PEAR_CHAIN_ID),
            vaults,
            token_cache,
            include_latest=True,
            vault_addresses=set(PEAR_FIRST_DEPOSIT_BLOCKS),
        )

        prices = pq.read_table(price_path).to_pandas()
        print(format_performance_table(database, prices))


if __name__ == "__main__":
    main()
