"""Do a discovery scan for ERC-4626 vaults on a chain.

- Discover new vaults on a chain
- Store the metadata in a vault database file
- Support incremental scanning

Usage:

.. code-block:: shell

    export JSON_RPC_URL=...
    python scripts/erc-4626/scan-vaults.py

Or for faster small sample scan limit the end block:

    END_BLOCK=5555721 python scripts/erc-4626/scan-vaults.py

"""

import logging
import os
from pathlib import Path
from urllib.parse import urlparse


from eth_defi.erc_4626.lead_scan_core import scan_leads
from eth_defi.utils import setup_console_logging

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

    setup_console_logging(log_file=Path(f"logs/scan-vaults.log"))

    end_block = os.environ.get("END_BLOCK")

    output_folder = os.environ.get("OUTPUT_FOLDER")
    if output_folder is None:
        output_folder = Path("~/.tradingstrategy/vaults").expanduser()
    else:
        output_folder = Path(output_folder).expanduser()

    os.makedirs(output_folder, exist_ok=True)

    vault_db_file = output_folder / f"vault-metadata-db.pickle"

    scan_leads(
        json_rpc_urls=JSON_RPC_URL,
        vault_db_file=vault_db_file,
        max_workers=max_workers,
        start_block=None,
        end_block=end_block,
        printer=print,
        backend="auto",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Fatal error: %s", e, exc_info=e)
        raise e
