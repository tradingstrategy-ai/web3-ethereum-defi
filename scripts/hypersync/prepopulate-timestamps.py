"""Create a multichain timesstamp database and prepopulate it with data from Hypersync.

To run a single chain:

.. code-block:: shell

    RPC_NAMES=JSON_RPC_ARBITRUM python scripts/hypersync/prepopulate-timestamps.py

"""

import os

import hypersync

from eth_defi.chain import get_chain_name
from eth_defi.event_reader.multicall_timestamp import fetch_block_timestamps_multiprocess_auto_backend
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.timestamp import get_hypersync_block_height
from eth_defi.provider.multi_provider import create_multi_provider_web3, MultiProviderWeb3Factory

RPC_NAMES = [
    "JSON_RPC_UNICHAIN",
    "JSON_RPC_SONIC",
    "JSON_RPC_HYPERLIQUID",
    "JSON_RPC_AVALANCHE",
    "JSON_RPC_ARBITRUM",
    "JSON_RPC_MODE",
    "JSON_RPC_MANTLE",
    "JSON_RPC_MANTLE_2",
    "JSON_RPC_BINANCE",
    "JSON_RPC_OPTIMISM",
    "JSON_RPC_ABSTRACT",
    "JSON_RPC_CELO",
    "JSON_RPC_SONEIUM",
    "JSON_RPC_ZKSYNC",
    "JSON_RPC_GNOSIS",
    "JSON_RPC_BLAST",
    "JSON_RPC_ZORA",
    "JSON_RPC_INK",
    "JSON_RPC_BASE",
    "JSON_RPC_POLYGON",
    "JSON_RPC_HEMI",
    "JSON_RPC_LINEA",
    "JSON_RPC_TAC",
    "JSON_RPC_PLASMA",
    "JSON_RPC_KATANA",
]


def create_and_populate_hypersync_timestamp_db_for_rpc(rpc_name: str):
    rpc_url = os.environ.get(rpc_name)
    assert rpc_url, f"Environment variable {rpc_name} not set"
    web3 = create_multi_provider_web3(rpc_url)
    web3factory = MultiProviderWeb3Factory(rpc_url)
    chain_id = web3.eth.chain_id
    chain_name = get_chain_name(chain_id)
    hypersync_server = get_hypersync_server(chain_id)
    if hypersync_server is None:
        print(f"No Hypersync server configured for chain {chain_name} ({chain_id}), skipping...")
        return

    hypersync_api_key = os.environ.get("HYPERSYNC_API_KEY")

    if hypersync_server:
        print(f"Using Hypersync server {hypersync_server} for chain {chain_name} ({chain_id})")
        hypersync_client = hypersync.HypersyncClient(
            hypersync.ClientConfig(
                url=hypersync_server,
                bearer_token=hypersync_api_key,
            )
        )
        last_block = get_hypersync_block_height(hypersync_client)

    else:
        print(f"No Hypersync server configured for chain {chain_name} ({chain_id}), skipping...")
        last_block = web3.eth.block_number
        hypersync_client = None

    timestamps = fetch_block_timestamps_multiprocess_auto_backend(
        web3factory=web3factory,
        chain_id=chain_id,
        start_block=1,
        end_block=last_block,
        step=1,
        hypersync_client=hypersync_client,
    )
    print("Fetched timestamps for", len(timestamps), "blocks on chain", chain_name)


def main():
    print(f"Prepopulating timestamp cache file {DEFAULT_TIMESTAMP_CACHE_FOLDER}")

    rpc_names = os.environ.get("RPC_NAMES")
    if rpc_names:
        rpc_names = rpc_names.split(" ")
    else:
        rpc_names = RPC_NAMES

    for rpc in rpc_names:
        print(f"Prepopulating timestamps for RPC {rpc}...")
        create_and_populate_hypersync_timestamp_db_for_rpc(rpc)
        print(f"Done prepopulating timestamps for RPC {rpc}.")

    print("All done.")


if __name__ == "__main__":
    main()
