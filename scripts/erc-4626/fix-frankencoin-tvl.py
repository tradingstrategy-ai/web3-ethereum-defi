"""Correct historical Frankencoin savings TVL in the uncleaned price parquet.

Before :class:`eth_defi.erc_4626.vault_protocol.frankencoin.vault.FrankencoinHistoricalReader`
was added, the generic ERC-4626 reader wrote ``svZCHF.totalAssets()`` to
``total_assets``. For Frankencoin this underreports product TVL because most
ZCHF is held directly by the underlying savings module.

This script updates only Frankencoin rows in ``vault-prices-1h.parquet``. It
keeps existing share-price samples and replaces ``total_assets`` with:

``ZCHF.balanceOf(savings_module) + ZCHF.balanceOf(svZCHF_wrapper)``

Usage:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/fix-frankencoin-tvl.py

Environment variables:

- ``UNCLEANED_PRICE_DATABASE``: Path to the uncleaned price parquet. Defaults to
  ``~/.tradingstrategy/vaults/vault-prices-1h.parquet``.
- ``DRY_RUN``: Set to ``true`` to only report without modifying files.
- ``START_BLOCK``: Optional inclusive block-number lower bound.
- ``END_BLOCK``: Optional inclusive block-number upper bound.
- ``MAX_WORKERS``: Optional per-chain parallel RPC workers. Default: 8.
- ``JSON_RPC_<CHAIN_NAME>``: Archive RPC URLs for affected chains, e.g.
  ``JSON_RPC_ETHEREUM``, ``JSON_RPC_BASE`` and ``JSON_RPC_GNOSIS``.

After running this script, rerun price post-processing and data export so
cleaned parquet and JSON outputs pick up the corrected TVL.
"""

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
from joblib import Parallel, delayed
from web3 import Web3

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.vault_protocol.frankencoin.vault import (
    FRANKENCOIN_BASE_SAVINGS_VAULT,
    FRANKENCOIN_ETHEREUM_SAVINGS_VAULT,
    FRANKENCOIN_GNOSIS_SAVINGS_VAULT,
    FRANKENCOIN_SAVINGS_VAULT_ABI,
)
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.vaultdb import DEFAULT_UNCLEANED_PRICE_DATABASE

logger = logging.getLogger(__name__)


#: Minimal ERC-20 ABI needed for historical ZCHF balances.
ERC20_BALANCE_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

#: Minimal ERC-4626 ABI needed to find the underlying ZCHF address.
ERC4626_ASSET_ABI = [
    {"inputs": [], "name": "asset", "outputs": [{"name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
]

#: Official Frankencoin Savings Vault addresses supported by this repository.
FRANKENCOIN_VAULT_SPECS = frozenset(
    {
        VaultSpec(1, FRANKENCOIN_ETHEREUM_SAVINGS_VAULT),
        VaultSpec(8453, FRANKENCOIN_BASE_SAVINGS_VAULT),
        VaultSpec(100, FRANKENCOIN_GNOSIS_SAVINGS_VAULT),
    }
)


@dataclass(slots=True, frozen=True)
class FrankencoinVaultContracts:
    """On-chain contracts needed to repair one Frankencoin vault.

    :param chain_id:
        EVM chain id.
    :param vault_address:
        svZCHF wrapper address.
    :param savings_module_address:
        Underlying Frankencoin savings module address.
    :param zchf:
        ZCHF ERC-20 contract.
    """

    chain_id: int
    vault_address: str
    savings_module_address: str
    zchf: object


@dataclass(slots=True, frozen=True)
class FrankencoinTvlRepairResult:
    """Result of repairing Frankencoin TVL rows.

    :param matched_rows:
        Rows selected for Frankencoin repair after block filters.
    :param updated_rows:
        Rows whose ``total_assets`` value changed.
    :param skipped_rows:
        Frankencoin rows not repaired because an RPC read failed.
    """

    matched_rows: int
    updated_rows: int
    skipped_rows: int


def parse_bool_env(name: str, *, default: bool = False) -> bool:
    """Parse a boolean environment variable.

    :param name:
        Environment variable name.
    :param default:
        Default value when unset.

    :return:
        Parsed boolean value.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def read_optional_int_env(name: str) -> int | None:
    """Read an optional integer environment variable.

    :param name:
        Environment variable name.

    :return:
        Parsed integer, or ``None``.
    """
    value = os.environ.get(name)
    if not value:
        return None
    return int(value)


def get_chain_rpc_env_name(chain_id: int) -> str:
    """Return the JSON-RPC environment variable for a chain.

    :param chain_id:
        EVM chain id.

    :return:
        Environment variable name.
    """
    chain_name = get_chain_name(chain_id).upper().replace(" ", "_")
    return f"JSON_RPC_{chain_name}"


def create_backup_path(parquet_path: Path) -> Path:
    """Create a non-conflicting backup path for the parquet file.

    :param parquet_path:
        Original parquet path.

    :return:
        Backup path with ``.bak-frankencoin-tvl`` suffix.
    """
    base_backup_path = parquet_path.with_suffix(".parquet.bak-frankencoin-tvl")
    if not base_backup_path.exists():
        return base_backup_path

    index = 1
    while True:
        backup_path = parquet_path.with_suffix(f".parquet.bak-frankencoin-tvl.{index}")
        if not backup_path.exists():
            return backup_path
        index += 1


def build_frankencoin_mask(
    df: pd.DataFrame,
    *,
    start_block: int | None = None,
    end_block: int | None = None,
) -> pd.Series:
    """Build a boolean mask for Frankencoin rows to repair.

    :param df:
        Uncleaned price DataFrame.
    :param start_block:
        Optional inclusive lower block bound.
    :param end_block:
        Optional inclusive upper block bound.

    :return:
        Boolean row mask.
    """
    spec_pairs = {(spec.chain_id, spec.vault_address.lower()) for spec in FRANKENCOIN_VAULT_SPECS}
    mask = pd.Series(
        [(int(chain), str(address).lower()) in spec_pairs for chain, address in zip(df["chain"], df["address"])],
        index=df.index,
    )
    if start_block is not None:
        mask &= df["block_number"] >= start_block
    if end_block is not None:
        mask &= df["block_number"] <= end_block
    return mask


def fetch_frankencoin_vault_contracts(web3: Web3, spec: VaultSpec) -> FrankencoinVaultContracts:
    """Fetch contracts needed to correct one Frankencoin vault.

    :param web3:
        Web3 instance for ``spec.chain_id``.
    :param spec:
        Frankencoin vault spec.

    :return:
        Contract bundle for historical TVL reads.
    """
    vault_address = Web3.to_checksum_address(spec.vault_address)
    vault_contract = web3.eth.contract(address=vault_address, abi=FRANKENCOIN_SAVINGS_VAULT_ABI + ERC4626_ASSET_ABI)
    savings_module_address = Web3.to_checksum_address(vault_contract.functions.savings().call())
    asset_address = Web3.to_checksum_address(vault_contract.functions.asset().call())
    zchf = web3.eth.contract(address=asset_address, abi=ERC20_BALANCE_ABI)
    return FrankencoinVaultContracts(
        chain_id=spec.chain_id,
        vault_address=vault_address,
        savings_module_address=savings_module_address,
        zchf=zchf,
    )


def fetch_frankencoin_total_assets_raw(contracts: FrankencoinVaultContracts, block_number: int) -> int:
    """Fetch raw Frankencoin savings product TVL at a historical block.

    :param contracts:
        Contract bundle for the vault.
    :param block_number:
        Historical block number.

    :return:
        Raw 18-decimal ZCHF amount.
    """
    savings_balance = contracts.zchf.functions.balanceOf(contracts.savings_module_address).call(block_identifier=block_number)
    wrapper_balance = contracts.zchf.functions.balanceOf(contracts.vault_address).call(block_identifier=block_number)
    return int(savings_balance) + int(wrapper_balance)


def decimalise_zchf_raw(raw_amount: int) -> float:
    """Convert a raw ZCHF amount to decimal units.

    :param raw_amount:
        Raw 18-decimal ZCHF amount.

    :return:
        Decimalised ZCHF amount as a float suitable for parquet storage.
    """
    return float(Decimal(raw_amount) / Decimal(10**18))


def repair_frankencoin_rows(
    df: pd.DataFrame,
    *,
    fetch_total_assets: Callable[[int, int, str], float],
    start_block: int | None = None,
    end_block: int | None = None,
    max_workers: int = 8,
) -> tuple[pd.DataFrame, FrankencoinTvlRepairResult]:
    """Repair Frankencoin rows in a price DataFrame.

    :param df:
        Uncleaned price DataFrame.
    :param fetch_total_assets:
        Callback ``(chain_id, block_number, vault_address) -> total_assets``.
    :param start_block:
        Optional inclusive lower block bound.
    :param end_block:
        Optional inclusive upper block bound.
    :param max_workers:
        Number of parallel workers.

    :return:
        Updated DataFrame and repair summary.
    """
    mask = build_frankencoin_mask(df, start_block=start_block, end_block=end_block)
    matched_df = df[mask]
    if matched_df.empty:
        return df.copy(), FrankencoinTvlRepairResult(matched_rows=0, updated_rows=0, skipped_rows=0)

    rows = [(index, int(row.chain), int(row.block_number), str(row.address).lower(), float(row.total_assets)) for index, row in matched_df[["chain", "block_number", "address", "total_assets"]].iterrows()]

    def repair_one(index: int, chain_id: int, block_number: int, address: str, old_total_assets: float) -> tuple[int, float | None, bool]:
        try:
            new_total_assets = fetch_total_assets(chain_id, block_number, address)
        except Exception:
            logger.exception("Could not fetch Frankencoin TVL for chain=%d address=%s block=%d", chain_id, address, block_number)
            return index, None, False
        return index, new_total_assets, new_total_assets != old_total_assets

    repaired = Parallel(n_jobs=max_workers, backend="threading")(delayed(repair_one)(index, chain_id, block_number, address, old_total_assets) for index, chain_id, block_number, address, old_total_assets in rows)

    updated_df = df.copy()
    updated_rows = 0
    skipped_rows = 0
    for index, new_total_assets, changed in repaired:
        if new_total_assets is None:
            skipped_rows += 1
            continue
        updated_df.at[index, "total_assets"] = new_total_assets
        if changed:
            updated_rows += 1

    return updated_df, FrankencoinTvlRepairResult(
        matched_rows=len(rows),
        updated_rows=updated_rows,
        skipped_rows=skipped_rows,
    )


def repair_frankencoin_tvl_parquet(
    parquet_path: Path,
    *,
    dry_run: bool,
    start_block: int | None,
    end_block: int | None,
    max_workers: int,
) -> FrankencoinTvlRepairResult:
    """Repair Frankencoin rows in an uncleaned price parquet file.

    :param parquet_path:
        Path to the uncleaned price parquet.
    :param dry_run:
        If ``True``, do not write any files.
    :param start_block:
        Optional inclusive lower block bound.
    :param end_block:
        Optional inclusive upper block bound.
    :param max_workers:
        Number of per-chain parallel RPC workers.

    :return:
        Repair summary.
    """
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet file not found: {parquet_path}")

    logger.info("Reading uncleaned price parquet from %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    mask = build_frankencoin_mask(df, start_block=start_block, end_block=end_block)
    affected_df = df[mask]
    if affected_df.empty:
        logger.info("No Frankencoin rows found in %s", parquet_path)
        return FrankencoinTvlRepairResult(matched_rows=0, updated_rows=0, skipped_rows=0)

    per_vault = affected_df.groupby(["chain", "address"]).agg(
        rows=("address", "count"),
        first_block=("block_number", "min"),
        last_block=("block_number", "max"),
        first_timestamp=("timestamp", "min"),
        last_timestamp=("timestamp", "max"),
    )
    logger.info("Frankencoin rows selected for repair:")
    for (chain_id, address), row in per_vault.iterrows():
        logger.info(
            "  chain=%d address=%s rows=%d blocks=%d-%d timestamps=%s-%s",
            chain_id,
            address,
            row["rows"],
            row["first_block"],
            row["last_block"],
            row["first_timestamp"],
            row["last_timestamp"],
        )

    web3_by_chain: dict[int, Web3] = {}
    contracts_by_pair: dict[tuple[int, str], FrankencoinVaultContracts] = {}

    for chain_id, address in affected_df[["chain", "address"]].drop_duplicates().itertuples(index=False):
        chain_id = int(chain_id)
        rpc_env_name = get_chain_rpc_env_name(chain_id)
        rpc_url = os.environ.get(rpc_env_name)
        if not rpc_url:
            raise RuntimeError(f"Set {rpc_env_name} to repair Frankencoin TVL for chain {chain_id}")
        web3 = web3_by_chain.setdefault(chain_id, create_multi_provider_web3(rpc_url))
        spec = VaultSpec(chain_id, str(address).lower())
        contracts_by_pair[chain_id, str(address).lower()] = fetch_frankencoin_vault_contracts(web3, spec)

    def fetch_total_assets(chain_id: int, block_number: int, vault_address: str) -> float:
        contracts = contracts_by_pair[chain_id, vault_address.lower()]
        raw_total_assets = fetch_frankencoin_total_assets_raw(contracts, block_number)
        return decimalise_zchf_raw(raw_total_assets)

    updated_df, result = repair_frankencoin_rows(
        df,
        fetch_total_assets=fetch_total_assets,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
    )

    if dry_run:
        logger.info("DRY RUN: would update %d of %d matched Frankencoin rows", result.updated_rows, result.matched_rows)
        return result

    if result.skipped_rows:
        raise RuntimeError(f"Refusing to write {parquet_path}: {result.skipped_rows} Frankencoin rows failed RPC repair")

    if result.updated_rows == 0:
        logger.info("No Frankencoin total_assets values changed")
        return result

    backup_path = create_backup_path(parquet_path)
    logger.info("Creating backup at %s", backup_path)
    shutil.copy2(parquet_path, backup_path)
    logger.info("Writing repaired parquet to %s", parquet_path)
    VaultHistoricalRead.write_uncleaned_parquet(updated_df, parquet_path)
    return result


def main() -> None:
    """Run the Frankencoin TVL repair script."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))

    parquet_path = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", str(DEFAULT_UNCLEANED_PRICE_DATABASE))).expanduser()
    dry_run = parse_bool_env("DRY_RUN", default=False)
    start_block = read_optional_int_env("START_BLOCK")
    end_block = read_optional_int_env("END_BLOCK")
    max_workers = int(os.environ.get("MAX_WORKERS", "8"))

    if dry_run:
        logger.info("DRY RUN MODE - no files will be modified")

    result = repair_frankencoin_tvl_parquet(
        parquet_path,
        dry_run=dry_run,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
    )
    action = "would be " if dry_run else ""
    logger.info("Frankencoin rows matched: %d", result.matched_rows)
    logger.info("Frankencoin rows %supdated: %d", action, result.updated_rows)
    logger.info("Frankencoin rows skipped: %d", result.skipped_rows)

    if result.updated_rows:
        logger.info("")
        logger.info("Next steps:")
        logger.info("  poetry run python scripts/erc-4626/post-process-prices.py")
        logger.info("  poetry run python scripts/erc-4626/export-data-files.py")


if __name__ == "__main__":
    main()
