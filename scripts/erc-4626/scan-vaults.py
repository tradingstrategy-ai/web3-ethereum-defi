"""Scan all ERC-4626 vaults on Base.

- Set up a HyperSync based vault discovery client
- As the writing of this, we get 1108 leads on Base

Usage:

.. code-block:: shell

    export JSON_RPC_BASE=...
    python scripts/erc-4626/scan-vaults.py

Or for faster small sample:

    END_BLOCK=5555721 python scripts/erc-4626/scan-vaults.py

"""
import logging
import os
import sys
from urllib.parse import urlparse

from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory

JSON_RPC_BASE = os.environ.get('JSON_RPC_BASE')
if JSON_RPC_BASE is None:
    try:
        urlparse(JSON_RPC_BASE)
    except ValueError:
        raise ValueError(f"Invalid JSON_RPC_BASE URL: {JSON_RPC_BASE}")


def main():

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    web3factory = MultiProviderWeb3Factory(JSON_RPC_BASE)
    print(f"Scanning ERC-4626 vaults on chain {web3.eth.chain_id}")

    assert web3.eth.chain_id == 8453, "Hardcoded for Base for now"

    # https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks
    hypersync_url = "https://base.hypersync.xyz/"

    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

    start_block = 1

    end_block = os.environ.get("END_BLOCK")
    if end_block is None:
        end_block = web3.eth.block_number
    else:
        end_block = int(end_block)
    vault_discover = HypersyncVaultDiscover(web3, web3factory, client)
    result = list(vault_discover.scan_vaults(start_block, end_block))





if __name__ == '__main__':
    main()