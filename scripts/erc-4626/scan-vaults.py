"""Scan all ERC-4626 vaults on Base.

- Set up a HyperSync based vault discovery client
- As the writing of this, we get 1108 leads on Base
-

"""
import asyncio
import logging
import os
import sys
from urllib.parse import urlparse

from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover

try:
    import hypersync
except ImportError as e:
    raise ImportError("Install the library with optional HyperSync dependency to use this module") from e

from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_URL = os.environ.get('JSON_RPC_URL')
if JSON_RPC_URL is None:
    try:
        urlparse(JSON_RPC_URL)
    except ValueError:
        raise ValueError(f"Invalid JSON-RPC Base URL: {JSON_RPC_URL}")


async def main():

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    web3 = create_multi_provider_web3(JSON_RPC_URL)
    print(f"Scanning ERC-4626 vaults on chain {web3.eth.chain_id}")

    assert web3.eth.chain_id == 8453, "Hardcoded for Base for now"

    # https://docs.envio.dev/docs/HyperSync/hypersync-supported-networks
    hypersync_url = "https://base.hypersync.xyz/"

    client = hypersync.HypersyncClient(hypersync.ClientConfig(url=hypersync_url))

    start_block = 1
    end_block = web3.eth.block_number
    vault_discover = HypersyncVaultDiscover(web3, client)
    result = await vault_discover.scan_vaults(start_block, end_block)




if __name__ == '__main__':
    asyncio.run(main())