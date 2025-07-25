"""Scan all ERC-4626 vaults on Base.

- Set up a HyperSync based vault discovery client
- As the writing of this, we get 1108 leads on Base
- Takes environment variables ``JSON_RPC_URL``, ``LOG_LEVEL``, ``END_BLOCK``
- Save data to /tmp: both raw Python objects and Parquet dump

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

Or for faster small sample scan limit the end block:

    END_BLOCK=5555721 python scripts/erc-4626/scan-vaults.py

"""

import decimal
import logging
import os
import pickle
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from IPython.core.display_functions import display
from joblib import Parallel, delayed

from tqdm_loggable.auto import tqdm

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.named import get_provider_name
from eth_defi.utils import setup_console_logging

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory


logger = logging.getLogger(__name__)


# Read JSON_RPC_CONFIGURATION from the environment
JSON_RPC_URL = os.environ.get("JSON_RPC_URL")
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError as e:
        raise ValueError(f"Invalid JSON_RPC URL: {JSON_RPC_URL}") from e


def main():
    setup_console_logging()

    # How many CPUs / subprocess we use
    max_workers = 16
    # max_workers = 1  # To debug, set workers to 1

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_URL, retries=5)
    name = get_chain_name(web3.eth.chain_id)
    rpcs = get_provider_name(web3.provider)
    print(f"Scanning ERC-4626 vaults on chain {web3.eth.chain_id}: {name}, using rpcs: {rpcs}")

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

    start_block = 1

    end_block = os.environ.get("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        end_block = int(end_block)

    output_folder = os.environ.get("OUTPUT_FOLDER")
    if output_folder is None:
        output_folder = Path("~/.tradingstrategy/vaults").expanduser()
    else:
        output_folder = Path(output_folder).expanduser()

    os.makedirs(output_folder, exist_ok=True)

    # Create a scanner that uses web3, HyperSync and subprocesses
    vault_discover = HypersyncVaultDiscover(
        web3,
        web3factory,
        client,
        max_workers=max_workers,
    )

    # Perform vault discovery and categorisation,
    # so we get information which address contains which kind of a vault
    vault_detections = list(vault_discover.scan_vaults(start_block, end_block))

    # Prepare data export by reading further per-vault data using multiprocessing
    worker_processor = Parallel(n_jobs=max_workers)
    logger.info("Extracting remaining vault metadata for %d vaults", len(vault_detections))

    # Quite a mouthful line to create a row of output for each vault detection using subproces pool
    desc = f"Extracting vault metadata using {max_workers} workers"
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in tqdm(vault_detections, desc=desc))

    print(f"Total {len(rows)} vaults detected")

    chain = web3.eth.chain_id

    if len(rows) == 0:
        print(f"No vaults found on chain {chain}, not generating any database updates")
        sys.exit(0)

    df = pd.DataFrame(rows)
    # Parquet cannot export the raw Python objects,
    # so we remove columns that are marked Python-internal only
    df = df.drop(columns=[col for col in df.columns if col.startswith("_")])
    df = df.sort_values("First seen")

    #
    # Save raw data rows
    #

    output_fname = Path(f"{output_folder}/chain-{chain}-vaults.parquet")
    parquet_df = df.copy()
    parquet_df = parquet_df.fillna(pd.NA)  # fillna replaces None and NaN with pd.NA
    # Avoid funny number issues
    # pyarrow.lib.ArrowInvalid: ('Decimal precision out of range [1, 76]: 90', 'Conversion failed for column NAV with type object')
    parquet_df["Mgmt fee"] = pd.to_numeric(parquet_df["Mgmt fee"], errors="coerce").astype("float64")
    parquet_df["Perf fee"] = pd.to_numeric(parquet_df["Perf fee"], errors="coerce").astype("float64")
    parquet_df["Shares"] = pd.to_numeric(parquet_df["Shares"], errors="coerce").astype("float64")
    parquet_df["NAV"] = pd.to_numeric(parquet_df["NAV"], errors="coerce").astype("float64")
    print(f"Saving raw data to {output_fname}")
    parquet_df.to_parquet(output_fname)

    #
    # Save machine-readable output
    #

    # Save dict -> data mapping with raw data to be read in notebooks and such.
    # This will preserve raw vault detection objects.
    # Keyed by (chain id, address)
    data_dict = {r["_detection_data"].get_spec(): r for r in rows}
    output_fname = Path(f"{output_folder}/vault-db.pickle")
    print(f"Saving vault pickled database to {output_fname}")
    if not output_fname.exists():
        existing_db = {}
    else:
        existing_db = pickle.load(output_fname.open("rb"))
        assert type(existing_db) == dict, f"Got: {type(existing_db)}: {existing_db}"
    # Merge new results
    existing_db.update(data_dict)
    pickle.dump(existing_db, output_fname.open("wb"))
    print(f"Vault database has {len(existing_db)} entries")

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

    erc_7540s = [v for v in rows if ERC4626Feature.erc_7540_like in v["_detection_data"].features]
    print(f"Total: {len(df)} vaults detected")
    print(f"ERC-7540: {len(erc_7540s)} vaults detected")

    with pd.option_context("display.max_rows", None):
        display(df)


if __name__ == "__main__":
    main()
