"""A sample script to fetch Uniswap v3 data."""
import os

from eth_defi.uniswap_v3.events import fetch_events_to_csv

json_rpc_url = os.environ["JSON_RPC_URL"]
fetch_events_to_csv(json_rpc_url, output_folder=".")
