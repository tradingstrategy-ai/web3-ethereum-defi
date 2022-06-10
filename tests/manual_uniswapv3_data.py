"""A sample script to fetch Uniswap v3 data."""
import os

from eth_defi.uniswap_v3.events import fetch_events_to_csv
from eth_defi.uniswap_v3.liquidity import create_tick_csv, create_tick_delta_csv

json_rpc_url = os.environ["JSON_RPC_URL"]

fetch_events_to_csv(json_rpc_url, output_folder=".")

tick_delta_csv = create_tick_delta_csv("./uniswapv3-Mint.csv", "./uniswapv3-Burn.csv", ".")

create_tick_csv("./uniswapv3-tickdeltas.csv", ".")
