"""Do a discovery scan for ERC-4626 vaults on a chain.

- Discover new vaults on a chain
- Store the metadata in a vault database file
- Support incremental scanning

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

Or:

.. code-block:: shell

    # TAC
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_TAC MAX_GETLOGS_RANGE=1000 python scripts/erc-4626/scan-vaults.py

    # Arbitrum
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ARBITRUM python scripts/erc-4626/scan-vaults.py

    # Hyperliquid
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_HYPERLIQUID python scripts/erc-4626/scan-vaults.py

    # Mainnet
    SCAN_BACKEND=rpc LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_ETHEREUM python scripts/erc-4626/scan-vaults.py

    # Monad
    LOG_LEVEL=info JSON_RPC_URL=$JSON_RPC_MONAD python scripts/erc-4626/scan-vaults.py


Or for faster small sample scan limit the end block:

    END_BLOCK=5555721 python scripts/erc-4626/scan-vaults.py

"""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import DEFAULT_VAULT_DATABASE

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e


logger = logging.getLogger(__name__)

# Read JSON_RPC_CONFIGURATION from the environment
JSON_RPC_URL = os.environ.get("JSON_RPC_URL")
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError as e:
        raise ValueError(f"Invalid JSON_RPC URL: {JSON_RPC_URL}") from e


def main():
    # How many CPUs / subprocess we use
    max_workers = int(os.environ.get("MAX_WORKERS", "16"))
    # max_workers = 1  # To debug, set workers to 1

    default_log_level = os.environ.get("LOG_LEVEL", "warning")
    setup_console_logging(
        default_log_level=os.environ.get("LOG_LEVEL", "warning"),
        log_file=Path(f"logs/scan-vaults.log"),
    )

    logger.info("Using log level: %s", default_log_level)
    end_block = os.environ.get("END_BLOCK")

    os.makedirs(DEFAULT_VAULT_DATABASE.parent, exist_ok=True)
    vault_db_file = DEFAULT_VAULT_DATABASE

    # Debug bad RPCs
    max_getlogs_range = os.environ.get("MAX_GETLOGS_RANGE", None)
    if max_getlogs_range:
        max_getlogs_range = int(max_getlogs_range)

    # Rescan all leads
    reset_leads = os.environ.get("RESET_LEADS", None)

    # Choose a different scan mode
    scan_backend = os.environ.get("SCAN_BACKEND", "auto")

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY", None)

    if scan_backend == "auto":
        assert hypersync_api_key, f"HYPERSYNC_API_KEY must be set to use auto scan backend"

    try:
        scan_leads(
            json_rpc_urls=JSON_RPC_URL,
            vault_db_file=vault_db_file,
            max_workers=max_workers,
            start_block=None,
            end_block=end_block,
            printer=print,
            backend=scan_backend,
            max_getlogs_range=max_getlogs_range,
            reset_leads=reset_leads,
            hypersync_api_key=hypersync_api_key,
        )

        print("All ok")
    except Exception as e:
        print("Died with error: %s", e)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
