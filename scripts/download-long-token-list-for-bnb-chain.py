"""Get a long token list for BNB chain.

This is mostly to used to generate a large number to valid ERC-20 contract addresses.

Downloads trading pairs from TradingStrategy dataset.

You need to have tradingstrategy package installed:

.. code-block:: python

    pip install tradingstrategy

Then run:

.. code-block:: python

    export TRADING_STRATEGY_API_KEY="secret-token:tradingstrategy-..."
    python scripts/download-long-token-list-for-bnb-chain.py

This will save `tests/token-list.json`

"""
import json
import os
import logging
import sys

from tradingstrategy.client import Client

logger = logging.basicConfig(stream=sys.stdout, level=logging.INFO)

api_key = os.environ["TRADING_STRATEGY_API_KEY"]
client = Client.create_live_client(api_key)
pairs_table = client.fetch_pair_universe()
wanted_chain_id = 56  # BNB Chain
fname = "tests/bnb-chain-token-list.json"

# Set of all tokens added
tokens = {}

for batch in pairs_table.to_batches():
    d = batch.to_pydict()

    # Get base and quote token of all trading pairs

    # https://stackoverflow.com/a/55633193/315168
    for chain_id, symbol, address in zip(d["chain_id"], d["token0_symbol"], d["token0_address"]):
        if chain_id == wanted_chain_id:
            tokens[symbol] = address

    for chain_id, symbol, address in zip(d["chain_id"], d["token1_symbol"], d["token1_address"]):
        if chain_id == wanted_chain_id:
            tokens[symbol] = address

print(f"Found {len(tokens):,} tokens")

with open(fname, "wt") as out:
    json.dump(tokens, out)

print(f"Wrote {fname}")
