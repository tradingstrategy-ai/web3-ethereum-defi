"""Scan all ERC-4626 vaults on Base.

- Set up a HyperSync based vault discovery client
- As the writing of this, we get 1108 leads on Base
- Takes environment variables ``JSON_RPC_URL``, ``LOG_LEVEL``, ``END_BLOCK``

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
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from IPython.core.display_functions import display
from joblib import Parallel, delayed

from tqdm_loggable.auto import tqdm

from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.scan import create_vault_scan_record_subprocess
from eth_defi.hypersync.server import get_hypersync_server

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory


logger = logging.getLogger(__name__)


# Read JSON_RPC_CONFIGURATION from the environment
JSON_RPC_URL = os.environ.get('JSON_RPC_URL')
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError as e:
        raise ValueError(f"Invalid JSON_RPC URL: {JSON_RPC_URL}") from e


def main():

    log_level = os.environ.get('LOG_LEVEL', 'WARNING').upper()
    logging.basicConfig(level=log_level, stream=sys.stdout)

    # How many CPUs / subprocess we use
    max_workers = 12
    # max_workers = 1  # To debug, set workers to 1

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_URL)
    print(f"Scanning ERC-4626 vaults on chain {web3.eth.chain_id}")

    hypersync_url = get_hypersync_server(web3)
    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

    start_block = 1

    end_block = os.environ.get("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        end_block = int(end_block)

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
    desc = f"Extracting vault metadata"
    rows = worker_processor(delayed(create_vault_scan_record_subprocess)(web3factory, d, end_block) for d in tqdm(vault_detections, desc=desc))

    print(f"Total {len(rows)} vaults detected")
    df = pd.DataFrame(rows)

    df = df.sort_values("First seen")

    output_fname = Path("/tmp/vaults.parquet")
    print(f"Saving raw data to {output_fname}")
    df.to_parquet(output_fname)

    # Format DataFrame output for terminal
    df["First seen"] = df["First seen"].dt.strftime('%Y-%b-%d')
    # df["Address"] = df["Address"].apply(lambda x: x[0:8])  # Address is too wide in terminal
    df = df.set_index("Address")

    # Round dust to zero, drop to 4 decimals
    def round_below_epsilon(x, epsilon=Decimal("0.1"), round_factor=Decimal("0.001")):
        if isinstance(x, Decimal):
            x = Decimal('0') if abs(x) < epsilon else x
            if x < 1000:
                try:
                    x = x.quantize(round_factor)
                except decimal.InvalidOperation:
                    logger.warning("Cannot quantise: %s", x)
            else:
                # Decimals are not important in large values
                x = int(x)
            return x

        return x  # Not decimal

    # Apply the function to all elements in the DataFrame
    df = df.apply(lambda col: col.map(round_below_epsilon))

    with pd.option_context('display.max_rows', None):
        display(df)


if __name__ == '__main__':
    main()