"""A script to fetch all BNB Chain trading pairs.

Uses Trading Strategy API.

Install::

    pip install trading-strategy

Then get an API key:

- https://tradingstrategy.ai/trading-view/backtesting

Run:

.. code-block:: shell

    export TRADING_STRATEGY_API_KEY=...
    python scripts/fetch-bnb-chain-pairs.py

This will save `/tmp/bnb-chain-trading-pairs.json`.
The list is 20MB so we do not keep it in the repo.
"""

import os
from dataclasses import dataclass, field
from typing import List

import pyarrow as pa
from dataclasses_json import dataclass_json
from tradingstrategy.client import Client

#: Our API key to access the dataset
from tradingstrategy.pair import PairUniverse, DEXPair

api_key = os.environ["TRADING_STRATEGY_API_KEY"]

#: Where do we write the JSON output
out_file = "/tmp/bnb-chain-trading-pairs.json"

#: Which chain we scan
chain_id = 56


@dataclass_json
@dataclass
class PairAddressInfo:
    """Describe one entry in JSON output.

    Unlike in raw Uniswap trading pair output,
    we get base token and quote token sorted in human meaningful way:

    https://tradingstrategy.ai/docs/programming/referenceprice.html#determining-quote-token
    """

    #: E.g. "WBNB"
    base_token_symbol: str
    #: E.g. "BUSD"
    quote_token_symbol: str
    #: Address
    base_token_address: str
    #: Address
    quote_token_address: str
    #: Address
    pair_address: str

    def __repr__(self):
        return f"<{self.base_token_symbol}-{self.quote_token_symbol} at {self.pair}?>"


@dataclass_json
@dataclass
class PairDatabase:
    """Describe JSON file."""

    pairs: List[PairAddressInfo] = field(default_factory=list)


client = Client.create_live_client(api_key)

# Get pairs as pyarrow.Table.
# https://arrow.apache.org/docs/python/generated/pyarrow.Table.html
# This contains pairs for all blockchains so we filter it down.
print("Downloading data")
pair_table = client.fetch_pair_universe()

# This contains pairs for all blockchains so we filter it down to the desired chain
# https://stackoverflow.com/a/67005586/315168
print("Filtering")
pair_table = pair_table.filter(pa.compute.equal(pair_table["chain_id"], chain_id))

print("Preparing output")
# Convert to PairUniverse class that's easier to manipualte
pair_universe = PairUniverse.create_from_pyarrow_table(pair_table)

# Build pairs list
database = PairDatabase()

# Populate database's entry list with fields that are our interest
pair: DEXPair
for pair_id, pair in pair_universe.pairs.items():
    database.pairs.append(PairAddressInfo(pair.base_token_symbol, pair.quote_token_symbol, pair.base_token_address, pair.quote_token_address, pair.address))

# Dump it to the disk
with open(out_file, "wt") as out:
    out.write(database.to_json())
