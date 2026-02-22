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

    export JSON_RPC_URL=$JSON_RPC_KATANA
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_HEMI
    RESET_LEADS=true python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_ARBITRUM
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_MANTLE
    python scripts/erc-4626/scan-vaults.py
    python scripts/erc-4626/scan-prices.py

    export JSON_RPC_URL=$JSON_RPC_GNOSIS
    python scripts/erc-4626/scan-prices.py

Copy server-side run results back to the local machine:

.. code-block:: shell

    rsync -av --inplace --progress --exclude="tmp*" "vitalik7-tailscale:.tradingstrategy/vaults/*" ~/.tradingstrategy/vaults/
    rsync -av --inplace --progress "vitalik7-tailscale:.tradingstrategy/block-timestamps.*" ~/.tradingstrategy

Debug scan of a single vault:

.. code-block:: shell

    # Hype++ on Arbitrum
    VAULT_ID="42161-0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"  \
    JSON_RPC_URL=$JSON_RPC_ARBITRUM \
    READER_STATE_DATABASE=/tmp/reader-state.pickle \
    UNCLEANED_PRICE_DATABASE=/tmp/prices.parquet \
    python scripts/erc-4626/scan-prices.py

Scan multiple vaults (comma-separated, with whitespace trimming).
When VAULT_ID is set, saved reader states for those vaults are cleared
for a fresh scan, and parquet deletion is vault-aware (other vaults' data
is preserved):

.. code-block:: shell

    # All Ember vaults on Ethereum from scratch
    VAULT_ID="1-0xf3190a3ecc109f88e7947b849b281918c798a0c4, 1-0x373152feef81cc59502da2c8de877b3d5ae2e342, 1-0x0b9342c15143e8f54a83f887c280a922f4c48771, 1-0x821fc97196d47566b618d27515df2c5201cc4125, 1-0xde88c15bbc9c4254a147a964f1fc937bae12712e, 1-0xb920ed46dec7455d0caf52b357d9a9f55b4daeca, 1-0x7e1916fa3bb694d4e7a038771e8fe97222e775ca, 1-0x9be9294722f8aad37b11a9792be2c782182cafa2, 1-0x2b13311fd553e74b421d4ccc96e348f71e179dcf" \
    JSON_RPC_URL=$JSON_RPC_ETHEREUM \
    START_BLOCK=1 \
    python scripts/erc-4626/scan-prices.py

"""

import logging
import os
import pickle
import sys
from pathlib import Path

from urllib.parse import urlparse

from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.named import get_provider_name

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.chain import get_chain_name
from eth_defi.erc_4626.classification import HARDCODED_PROTOCOLS, create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import scan_historical_prices_to_parquet, pformat_scan_result
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE, DEFAULT_UNCLEANED_PRICE_DATABASE, DEFAULT_READER_STATE_DATABASE

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

    default_log_level = os.environ.get("LOG_LEVEL", "info")

    profile = os.environ.get("PROFILE", "false") == "true"

    setup_console_logging(
        default_log_level=default_log_level,
        log_file=Path(f"logs/{name.lower()}-vault-price-scan.log"),
    )

    logger.debug("Using log level: %s", default_log_level)
    logger.info("Using RPC: %s", get_provider_name(web3.provider))

    min_deposit_threshold = 5

    start_block = os.environ.get("START_BLOCK")
    if start_block is not None:
        start_block = int(start_block)

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

    # Scan specific vaults (comma-separated list)
    vault_id = os.environ.get("VAULT_ID")
    if vault_id is not None:
        vault_specs = [VaultSpec.parse_string(v.strip()) for v in vault_id.split(",")]
        vault_addresses = {spec.vault_address for spec in vault_specs}
        print(f"Filtering to {len(vault_addresses)} specific vaults")
    else:
        vault_specs = None
        vault_addresses = None

    frequency = os.environ.get("FREQUENCY", "1h")

    assert frequency in ["1h", "1d"], f"Unsupported frequency: {frequency}"

    hypersync_config = configure_hypersync_from_env(web3)

    print(f"Using scan backend: {hypersync_config.scan_backend}, HyperSync URL: {hypersync_config.hypersync_url}")

    reader_state_database = os.environ.get("READER_STATE_DATABASE")
    price_parquet_fname = os.environ.get("UNCLEANED_PRICE_DATABASE")

    vault_db_fname = DEFAULT_VAULT_DATABASE
    price_parquet_fname = Path(price_parquet_fname or DEFAULT_UNCLEANED_PRICE_DATABASE)
    reader_state_db = Path(reader_state_database or DEFAULT_READER_STATE_DATABASE)

    print(f"Scanning vault historical prices on chain {web3.eth.chain_id}: {name}")

    assert vault_db_fname.exists(), f"File {vault_db_fname} does not exist - run scan-vaults.py first"
    vault_db = pickle.load(vault_db_fname.open("rb"))

    if reader_state_db.exists():
        reader_states = pickle.load(reader_state_db.open("rb"))
        unique_chains = set(spec.chain_id for spec in reader_states.keys())
        print(f"Loaded {len(reader_states)} reader states from {reader_state_db}, contains {len(unique_chains)} chains")
    else:
        # Start with empty reader states:g first chain. first scan
        print(f"No existing reader states found at {reader_state_db}, starting fresh")
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

        if detection.deposit_count < min_deposit_threshold and address.lower() not in HARDCODED_PROTOCOLS:
            # print(f"Vault does not have enough deposits: {address}, has: {detection.deposit_count}, threshold {min_deposit_threshold}")
            continue

        if vault_addresses is not None:
            if address.lower() not in vault_addresses:
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

    if vault_addresses is not None and reader_states:
        # Fresh scan for selected vaults - remove their saved state
        # so they scan from the beginning
        cleared = sum(1 for spec in reader_states if spec.vault_address in vault_addresses)
        reader_states = {spec: state for spec, state in reader_states.items() if spec.vault_address not in vault_addresses}
        if cleared:
            print(f"Cleared {cleared} reader states for selected vaults (fresh scan)")

    if len(vaults) == 0:
        print(f"No vaults to scan on {name} after filtering, exiting")
        sys.exit(0)

    if profile:
        # cProfiler hook, as we want to make some stuff faster
        import cProfile

        pr = cProfile.Profile()
        pr.enable()
        profiler_file = Path("logs/scan-prices-profile.cprof")
        print(f"Profiling, output file: {profiler_file.resolve()}")
    else:
        pr = None

    try:
        scan_result = scan_historical_prices_to_parquet(
            output_fname=price_parquet_fname,
            web3=web3,
            web3factory=web3factory,
            vaults=vaults,
            start_block=start_block,
            end_block=end_block,
            max_workers=max_workers,
            chunk_size=32,
            token_cache=token_cache,
            frequency=frequency,
            reader_states=reader_states,
            hypersync_client=hypersync_config.hypersync_client,
            vault_addresses=vault_addresses,
        )
    finally:
        if pr:
            pr.disable()
            pr.dump_stats(profiler_file)

    # Save states
    states = scan_result["reader_states"]
    if states:
        print(f"Saving {len(states)} reader states to {reader_state_db}")
        # example_state = next(iter(states.values()))
        # print("Example state:\n", pformat(example_state))
        pickle.dump(states, reader_state_db.open("wb"))

        unique_chains = set(spec.chain_id for spec in states.keys())
        print(f"Reader states saved for {len(unique_chains)} chains")
    else:
        print("No states to save")

    token_cache.commit()
    print(f"Token cache size is {token_cache.get_file_size():,} bytes, {len(token_cache):,} tokens")
    print("Scan complete")
    print(pformat_scan_result(scan_result))
    print("All ok")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
