"""Aave liquidations scanner.

In this script we will show how to download liquidation events events from [Aave V3](https://tradingstrategy.ai/glossary/aave) to a Parquet file.

* See the corresponding notebook to analyse the data
* We use Envio's HyperSync service
* We download data for multiple EVM chains
* You need to configure the following environment variables to point to your JSON-RPC endpoints:
  * `JSON_RPC_ETHEREUM`
  * `JSON_RPC_POLYGON`
  * `JSON_RPC_ARBITRUM`
  * `JSON_RPC_BASE`
  * `JSON_RPC_BINANCE`
  * `JSON_RPC_PLASMA`

.. note ::

    This script collects data from all Aave-v3 like deployments. This includes scam and test deployments.
    You need to manually clean up the data from scams.

"""

import os
from pathlib import Path

import pandas as pd

from eth_defi.aave_v3.liquidation import AaveLiquidationReader
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.hypersync.timestamp import get_hypersync_block_height
from eth_defi.provider.multi_provider import create_multi_provider_web3

import hypersync

from eth_defi.provider.env import read_json_rpc_url, get_json_rpc_env


TARGET_CHAINS = {
    "binance": 56,
    "arbitrum": 42161,
    "base": 8453,
    "polygon": 137,
    "ethereum": 1,
    # "plasma": 9745,
}

PARQUET_PATH = Path.home() / ".tradingstrategy" / "liquidations" / "aave-v3-liquidations.parquet"
PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)


def main():
    df = pd.read_parquet(PARQUET_PATH) if PARQUET_PATH.exists() else pd.DataFrame()

    for chain_id in TARGET_CHAINS.values():
        env_name = get_json_rpc_env(chain_id)
        assert env_name in os.environ, f"Missing env: {env_name}"

    for chain_name, chain_id in TARGET_CHAINS.items():
        # Get JSON_RPC_ETHEREUM, JSON_RPC_POLYGON, ...
        rpc_url = read_json_rpc_url(chain_id)
        web3 = create_multi_provider_web3(rpc_url)
        # Create corresponding HyperSync client
        hypersync_server = get_hypersync_server(web3)
        config = hypersync.ClientConfig(url=hypersync_server)
        client = hypersync.HypersyncClient(config)

        # Continue where we left off this chain last time
        last_block = 1
        if not df.empty:
            existing_rows = df.loc[df.chain_id == chain_id]
            if not existing_rows.empty:
                last_block = existing_rows["block_number"].max() + 1

        end_block = get_hypersync_block_height(client)

        reader = AaveLiquidationReader(
            web3=web3,
            client=client,
        )

        events = reader.fetch_liquidations(
            start_block=last_block,
            end_block=end_block,
        )

        new_data = pd.DataFrame([e.as_row() for e in events])
        df = pd.concat([df, new_data], ignore_index=True)

        df.to_parquet(PARQUET_PATH, index=False)
        print(f"Chain {chain_name} done, total liquidation rows now {len(df):,}, file size is {PARQUET_PATH.stat().st_size / 1024**2:.2f} MiB")

    print("All ok")


if __name__ == "__main__":
    main()
