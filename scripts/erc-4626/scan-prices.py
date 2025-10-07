"""Scan historical vault share prices and fees.

- Scan prices for all vaults discovered earlier with ``scan-vaults.py``
- Write results to the Parquet file that is shared across all chains

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-prices.py

Or for faster small sample scan limit the end block::

    END_BLOCK=5555721 python scripts/erc-4626/scan-prices.py

Or for dynamic 1h frequency scan for Polygon, delete existing data::

    rm -rf ~/.tradingstrategy/vaults
    export FREQUENCY=1h
    export JSON_RPC_URL=$JSON_RPC_GNOSIS
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.pyH


Re-run manual test::

    export JSON_RPC_URL=$JSON_RPC_PLASMA
    python scripts/erc-4626/scan-vaults.py

    export JSON_RPC_URL=$JSON_RPC_LINEA
    RESET_LEADS=true python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_TAC
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_ARBITRUM
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_MANTLE
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_GNOSIS
    python scripts/erc-4626/scan-prices.py

Copy server-side run results back to the local machine::

    rsync -av --inplace --progress --exclude="tmp*" "poly:.tradingstrategy/vaults/*" ~/.tradingstrategy/vaults/

"""

import logging
import os
import pickle
import sys
from pathlib import Path
from pprint import pformat
from urllib.parse import urlparse

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.historical import scan_historical_prices_to_parquet, pformat_scan_result
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE

logger = logging.getLogger(__name__)

# Read JSON_RPC_CONFIGURATION from the environment
JSON_RPC_URL = os.environ.get("JSON_RPC_URL")
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError as e:
        raise ValueError(f"Invalid JSON_RPC URL: {JSON_RPC_URL}") from e


def main():
    token_cache = TokenDiskCache()

    # How many CPUs / subprocess we use
    max_workers = os.environ.get("MAX_WORKERS", "20")
    max_workers = int(max_workers)
    # max_workers = 1  # To debug, set workers to 1

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_URL, retries=5)
    name = get_chain_name(web3.eth.chain_id)

    setup_console_logging(log_file=Path(f"logs/{name}-scan-prices.log"))

    min_deposit_threshold = 5

    end_block = os.environ.get("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        end_block = int(end_block)

    chain_id = web3.eth.chain_id

    output_folder = os.environ.get("OUTPUT_FOLDER")
    if output_folder is None:
        output_folder = Path("~/.tradingstrategy/vaults").expanduser()
    else:
        output_folder = Path(output_folder).expanduser()

    frequency = os.environ.get("FREQUENCY", "1d")

    assert frequency in ["1h", "1d"], f"Unsupported frequency: {frequency}"

    vault_db_fname = DEFAULT_VAULT_DATABASE
    price_parquet_fname = output_folder / f"vault-prices-{frequency}.parquet"

    reader_state_db = output_folder / f"vault-reader-state-{frequency}.pickle"

    print(f"Scanning vault historical prices on chain {web3.eth.chain_id}: {name}")

    assert vault_db_fname.exists(), f"File {vault_db_fname} does not exist - run scan-vaults.py first"
    vault_db = pickle.load(vault_db_fname.open("rb"))

    if reader_state_db.exists():
        reader_states = pickle.load(reader_state_db.open("rb"))
        unique_chains = set(spec.chain_id for spec in reader_states.keys())
        print(f"Loaded {len(reader_states)} reader states from {reader_state_db}, contains {len(unique_chains)} chains")
    else:
        # Start with empty reader states:g first chain. first scan
        reader_states = {}

    chain_vaults = [v for v in vault_db.rows.values() if v["_detection_data"].chain == chain_id]
    print(f"Chain {name} has {len(chain_vaults):,} vaults in the vault detection database")

    if len(chain_vaults) == 0:
        print(f"No vaults on chain {name}")
        sys.exit(0)

    vaults = []
    start = 999_999_999_999
    for row in chain_vaults:
        detection: ERC4262VaultDetection
        detection = row["_detection_data"]
        address = detection.address

        if detection.deposit_count < min_deposit_threshold:
            # print(f"Vault does not have enough deposits: {address}, has: {detection.deposit_count}, threshold {min_deposit_threshold}")
            continue

        vault = create_vault_instance(web3, address, detection.features, token_cache=token_cache)
        if vault is not None:
            vault.first_seen_at_block = detection.first_seen_at_block
            vaults.append(vault)
            start = min(start, detection.first_seen_at_block)
        else:
            # print(f"Vault does not have a supported reader: {address}")
            pass

    print(f"After filtering vaults for non-interesting entries, we have {len(vaults):,} vaults left")

    scan_result = scan_historical_prices_to_parquet(
        output_fname=price_parquet_fname,
        web3=web3,
        web3factory=web3factory,
        vaults=vaults,
        start_block=None,
        end_block=end_block,
        max_workers=max_workers,
        chunk_size=32,
        token_cache=token_cache,
        frequency=frequency,
        reader_states=reader_states,
    )

    # Save states
    states = scan_result["reader_states"]
    if states:
        print(f"Saving {len(states)} reader states to {reader_state_db}")
        example_state = next(iter(states.values()))
        print("Example state:\n", pformat(example_state))
        pickle.dump(states, reader_state_db.open("wb"))

        unique_chains = set(spec.chain_id for spec in states.keys())
        print(f"Reader states saved for {len(unique_chains)} chains")
    else:
        print("No states to save")

    token_cache.commit()
    print(f"Token cache size is {token_cache.get_file_size():,} bytes, {len(token_cache):,} tokens")
    print("Scan complete")
    print(pformat_scan_result(scan_result))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
