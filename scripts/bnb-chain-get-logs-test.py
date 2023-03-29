"""Test that BNB Chain node does good eth_getLogs

- Ask 100 blocks worth of events

- Manually set `timeout` parameter for Web3 HTTP connection

Manually test eth_getLogs with curl:

.. code-block:: shell

    export JSON_RPC_BINANCE="https://bsc-mainnet.nodereal.io/v1/64a9df0874fb4a93b9d0a3849de012d3"

    curl \
        --location \
        --header 'Content-Type: application/json' \
        --request POST $JSON_RPC_BINANCE \
        --data-raw '{"jsonrpc": "2.0", "method": "eth_getLogs", "params": [{"topics": [["0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"]], "fromBlock": "0xd59f80", "toBlock": "0xd59fe3", "address": "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"}], "id": 10}'
"""

import logging
import os
import sys
import textwrap

import requests

from web3 import HTTPProvider, Web3
from web3.middleware import geth_poa_middleware

from eth_defi.abi import get_contract
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.reader import read_events
from eth_defi.uniswap_v2.pair import fetch_pair_details


logger = logging.getLogger(__name__)


TIMEOUT = 30.0


def print_roundtrip(response, *args, **kwargs):
    format_headers = lambda d: "\n".join(f"{k}: {v}" for k, v in d.items())
    print(
        textwrap.dedent(
            """
        ---------------- request ----------------
        {req.method} {req.url}
        {reqhdrs}

        {req.body}
        ---------------- response ----------------
        {res.status_code} {res.reason} {res.url}
        {reshdrs}

        {res.text}
    """
        ).format(
            req=response.request,
            res=response,
            reqhdrs=format_headers(response.request.headers),
            reshdrs=format_headers(response.headers),
        )
    )


def main():
    logger = logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

    json_rpc_url = os.environ["JSON_RPC_BINANCE"]

    session = requests.Session()
    session.hooks["response"].append(print_roundtrip)

    web3 = Web3(HTTPProvider(json_rpc_url, request_kwargs={"timeout": TIMEOUT}, session=session))
    web3.middleware_onion.clear()
    web3.middleware_onion.inject(geth_poa_middleware, layer=0)

    bnb_busd_pair_address = "0x58F876857a02D6762E0101bb5C46A8c1ED44Dc16"

    Pair = get_contract(web3, "UniswapV2Pair.json")

    signatures = Pair.events.Sync.build_filter().topics
    assert len(signatures) == 1

    filter = Filter(
        contract_address=bnb_busd_pair_address,
        bloom=None,
        topics={
            signatures[0]: Pair.events.Sync,
        },
    )

    pool_details = fetch_pair_details(web3, bnb_busd_pair_address)
    print("Pair details are", pool_details)

    # Randomly chosen block range.
    # 100 blocks * 3 sec / block = ~300 seconds
    start_block = 14_000_000
    end_block = 14_000_100

    results = []
    for log_result in read_events(
        web3,
        start_block,
        end_block,
        [Pair.events.Sync],
        notify=None,
        chunk_size=100,
        filter=filter,
    ):
        results.append(log_result)

    print(f"Read {len(results)} logs")


if __name__ == "__main__":
    main()
