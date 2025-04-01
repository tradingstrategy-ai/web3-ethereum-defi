"""Read share prices of all vaults.

- For previously discovered vaults, read their share prices
- Run ``scripts/erc-4626/scan-vaults-all-chain.sh`` first
"""

import decimal
import logging
import os
import pickle
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
from IPython.core.display_functions import display
from joblib import Parallel, delayed

from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess

from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory


logger = logging.getLogger(__name__)

def main():

    log_level = os.environ.get('LOG_LEVEL', 'WARNING').upper()
    logging.basicConfig(level=log_level, stream=sys.stdout)

    # How many CPUs / subprocess we use
    max_workers = 12
    # max_workers = 1  # To debug, set workers to 1

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_URL, retries=5)
    print(f"Scanning ERC-4626 historical prices on chain {web3.eth.chain_id}")

    start_block = 1

    end_block = os.environ.get("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        end_block = int(end_block)

    output_folder = os.environ.get("OUTPUT_FOLDER")
    if output_folder is None:
        output_folder = "/tmp"
    else:
        output_folder = Path(output_folder).expanduser()

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
    df = pd.DataFrame(rows)
    # Cannot export the raw Python object,
    # this is for the pickle only
    df = df.drop(columns="_detection_data")
    df = df.sort_values("First seen")

    #
    # Save raw data rows
    #

    chain = web3.eth.chain_id
    output_fname = Path(f"{output_folder}/chain-{chain}-vaults.parquet")
    parquet_df = df.copy()
    parquet_df = parquet_df.fillna(pd.NA)  # fillna replaces None and NaN with pd.NA
    parquet_df['Mgmt fee'] = pd.to_numeric(parquet_df['Mgmt fee'], errors='coerce')
    parquet_df['Perf fee'] = pd.to_numeric(parquet_df['Perf fee'], errors='coerce')
    print(f"Saving raw data to {output_fname}")
    parquet_df.to_parquet(output_fname)

    #
    # Save machine-readable output
    #

    # Save dict -> data mapping with raw data to be read in notebooks and such.
    # This will preserve raw vault detection objects.
    data_dict = {r["Address"]: r for r in rows}
    output_fname = Path(f"{output_folder}/chain-{chain}-vaults.pickle")
    print(f"Saving raw data to {output_fname}")
    pickle.dump(data_dict, output_fname.open("wb"))

    #
    # Display in terminal
    #

    # Format DataFrame output for terminal
    df["First seen"] = df["First seen"].dt.strftime('%Y-%b-%d')
    df["Mgmt fee"] = df["Mgmt fee"].apply(lambda x: f"{x:.1%}" if type(x) == float else "-")
    df["Perf fee"] = df["Perf fee"].apply(lambda x: f"{x:.1%}" if type(x) == float else "-")
    # df["Address"] = df["Address"].apply(lambda x: x[0:8])  # Address is too wide in terminal
    df = df.set_index("Address")

    # Round dust to zero, drop to 4 decimals
    def round_below_epsilon(x, epsilon=Decimal("0.1"), round_factor=Decimal("0.001")):
        if isinstance(x, Decimal):

            # Eliminate dust
            x = Decimal('0') if abs(x) < epsilon else x

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

    with pd.option_context('display.max_rows', None):
        display(df)


if __name__ == '__main__':
    main()