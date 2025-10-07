"""Core logic for scanning the vault leads.

- Does not scan historical prices, but only discovers vaults
"""

import decimal
import logging
import pickle
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd

from joblib import Parallel, delayed
from IPython.core.display_functions import display

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.discovery_base import LeadScanReport
from eth_defi.erc_4626.rpc_discovery import JSONRPCVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.timestamp import get_hypersync_block_height
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.provider.named import get_provider_name
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)


def display_vaults_table(df: pd.DataFrame):
    #
    # Display in terminal
    #

    # Format DataFrame output for terminal
    df["First seen"] = df["First seen"].dt.strftime("%Y-%b-%d")
    df["Mgmt fee"] = df["Mgmt fee"].apply(lambda x: f"{x:.1%}" if type(x) == float else "-")
    df["Perf fee"] = df["Perf fee"].apply(lambda x: f"{x:.1%}" if type(x) == float else "-")
    # df["Address"] = df["Address"].apply(lambda x: x[0:8])  # Address is too wide in terminal
    df = df.set_index("Address")

    # Round dust to zero, drop to 4 decimals
    def round_below_epsilon(x, epsilon=Decimal("0.1"), round_factor=Decimal("0.001")):
        if isinstance(x, Decimal):
            # Eliminate dust
            x = Decimal("0") if abs(x) < epsilon else x

            float_x = float(x)

            # Get rid of numbers with too many digits
            if float_x >= 1e12:  # Trillions
                return f"{float_x / 1e12:.1f}T"
            elif float_x >= 1e9:  # Billions
                return f"{float_x / 1e9:.1f}G"
            elif float_x >= 1e6:  # Millions
                return f"{float_x / 1e6:.1f}M"
            elif float_x >= 1e3:  # Millions
                return f"{float_x / 1e6:.1f}K"
            else:
                try:
                    x = x.quantize(round_factor)
                except decimal.InvalidOperation:
                    logger.warning("Cannot quantise: %s", x)

        return x  # Not decimal

    # Apply the function to all elements in the DataFrame
    df = df.apply(lambda col: col.map(round_below_epsilon))

    with pd.option_context("display.max_rows", None):
        display(df)


def scan_leads(
    json_rpc_urls: str,
    vault_db_file: Path,
    max_workers: int = 16,
    start_block: int | None = None,
    end_block: int | None = None,
    printer=print,
    backend: Literal["auto", "hypersync", "rpc"] = "auto",
    max_getlogs_range: int | None = None,
) -> LeadScanReport:
    """Core loop to discover new vaults on a chain.

    - Use Hypersync if available, otherwise fall back to JSON-RPC only scanning
    - Resume for the last known block
    """

    # Avoid hard dependency
    import hypersync
    from eth_defi.hypersync.server import get_hypersync_server
    from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover

    assert isinstance(vault_db_file, Path)

    web3 = create_multi_provider_web3(json_rpc_urls)
    web3factory = MultiProviderWeb3Factory(json_rpc_urls, retries=5)

    chain_id = web3.eth.chain_id
    name = get_chain_name(chain_id)
    rpcs = get_provider_name(web3.provider)

    match backend:
        case "auto":
            hypersync_url = get_hypersync_server(web3, allow_missing=True)
            if hypersync_url:
                hypersync_client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))
            else:
                hypersync_client = None
        case "hypersync":
            hypersync_url = get_hypersync_server(web3)
            assert hypersync_url, f"No HyperSync server available for chain {web3.eth.chain_id}"
            hypersync_client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))
        case "rpc":
            hypersync_client = None

    printer(f"Scanning ERC-4626 vaults on chain {web3.eth.chain_id}: {name}, using rpcs: {rpcs}, using HyperSync: {hypersync_url or '<not avail>'}, and {max_workers} workers")

    if not vault_db_file.exists():
        logger.info("Starting vault lead scan, created new database at %s", vault_db_file)
        existing_db = VaultDatabase()
    else:
        logger.info("Starting vault lead scan, using database at %s", vault_db_file)
        existing_db = VaultDatabase.read(vault_db_file)
        assert type(existing_db) == VaultDatabase, f"Got: {type(existing_db)}: {existing_db}"

    if start_block is None:
        start_block = existing_db.get_chain_start_block(web3.eth.chain_id)

    if end_block is None:
        end_block = web3.eth.block_number
    else:
        assert type(end_block) == int

    if hypersync_client:
        # Create a scanner that uses web3, HyperSync and subprocesses
        vault_discover = HypersyncVaultDiscover(
            web3,
            web3factory,
            hypersync_client,
            max_workers=max_workers,
        )

        if not end_block:
            end_block = get_hypersync_block_height(hypersync_client)

    else:
        # Create a scanner that uses web3 and subprocesses
        vault_discover = JSONRPCVaultDiscover(
            web3,
            web3factory,
            max_workers=max_workers,
            max_getlogs_range=max_getlogs_range,
        )
        if not end_block:
            end_block = web3.eth.block_number

    vault_discover.seed_existing_leads(existing_db.get_existing_leads_by_chain(chain_id))

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    report = vault_discover.scan_vaults(start_block, end_block)
    vault_detections = list(report.detections.values())

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=max_workers)
    logger.info("Extracting remaining vault metadata for %d vaults", len(vault_detections))

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    desc = f"Extracting vault metadata using {max_workers} workers"
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in tqdm(vault_detections, desc=desc))

    printer(f"Total {len(rows)} vaults detected")

    chain = web3.eth.chain_id

    if len(rows) == 0:
        printer(f"No vaults found on chain {chain}, not generating any database updates")
        return LeadScanReport()

    df = pd.DataFrame(rows)
    # Parquet cannot export the raw Python objects,
    # so we remove columns that are marked Python-internal only
    df = df.drop(columns=[col for col in df.columns if col.startswith("_")])
    df = df.sort_values("First seen")

    #
    # Save raw data rows
    #

    # output_fname = Path(f"{output_folder}/chain-{chain}-vaults.parquet")
    # parquet_df = df.copy()
    # parquet_df = parquet_df.fillna(pd.NA)  # fillna replaces None and NaN with pd.NA
    # # Avoid funny number issues
    # # pyarrow.lib.ArrowInvalid: ('Decimal precision out of range [1, 76]: 90', 'Conversion failed for column NAV with type object')
    # parquet_df["Mgmt fee"] = pd.to_numeric(parquet_df["Mgmt fee"], errors="coerce").astype("float64")
    # parquet_df["Perf fee"] = pd.to_numeric(parquet_df["Perf fee"], errors="coerce").astype("float64")
    # parquet_df["Shares"] = pd.to_numeric(parquet_df["Shares"], errors="coerce").astype("float64")
    # parquet_df["NAV"] = pd.to_numeric(parquet_df["NAV"], errors="coerce").astype("float64")
    # print(f"Saving raw data to {output_fname}")
    # parquet_df.to_parquet(output_fname)

    #
    # Save machine-readable output
    #

    # Save dict -> data mapping with raw data to be read in notebooks and such.
    # This will preserve raw vault detection objects.
    # Keyed by (chain id, address)
    data_dict = {r["_detection_data"].get_spec(): r for r in rows}
    report.rows = data_dict

    printer(f"Saving vault pickled database to {vault_db_file}")
    # Merge new results
    existing_db.update_leads_and_rows(
        chain_id=chain_id,
        last_scanned_block=end_block,
        leads=report.leads,
        rows=data_dict,
    )
    existing_db.write(vault_db_file)
    printer(f"Vault database has {existing_db.get_lead_count()} entries")

    erc_7540s = [v for v in rows if ERC4626Feature.erc_7540_like in v["_detection_data"].features]
    printer(f"Total: {len(df)} vaults detected, last block is now {report.end_block:,}")
    # printer(f"ERC-7540: {len(erc_7540s)} vaults detected")

    display_vaults_table(df)

    return report
