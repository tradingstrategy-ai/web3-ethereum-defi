"""Multithread reader example.

See :ref:`multithread-reader` for full tutorial.
"""

import datetime
import logging
import os

from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from eth_defi.abi import get_contract
from eth_defi.chain import install_chain_middleware
from eth_defi.event_reader.conversion import decode_data, convert_uint256_bytes_to_address, convert_int256_bytes_to_int
from eth_defi.event_reader.filter import Filter
from eth_defi.event_reader.multithread import MultithreadEventReader
from eth_defi.event_reader.progress_update import PrintProgressUpdate
from eth_defi.token import fetch_erc20_details


def main():
    # Set up stdout logger
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper(), handlers=[logging.StreamHandler()])

    # Set up Web3 connection
    json_rpc_url = os.environ.get("JSON_RPC_POLYGON_FULL_NODE")
    assert json_rpc_url, f"You need to give JSON_RPC_POLYGON_FULL_NODE environment variable pointing ot your full node"

    web3 = Web3(HTTPProvider(json_rpc_url))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    start_block = os.environ.get("START_BLOCK")
    if start_block:
        start_block = int(start_block)
    else:
        start_block = 1

    end_block = os.environ.get("END_BLOCK")
    if end_block:
        end_block = int(end_block)
    else:
        end_block = web3.eth.block_number

    # Get one of the contracts prepackaged ABIs from eth_defi package
    value_interpreter_contract = get_contract(web3, "enzyme/ValueInterpreter.json")

    # Read events only for this contract
    # See https://docs.enzyme.finance/developers/contracts/polygon
    target_contract_address = "0x66De7e286Aae66f7f3Daf693c22d16EEa48a0f45"

    # Create eth_getLogs event filtering
    filter = Filter.create_filter(
        target_contract_address,
        [value_interpreter_contract.events.PrimitiveAdded],
    )

    # Set up multithreaded Polygon event reader.
    # Print progress to the console how many blocks there are left to read.
    reader = MultithreadEventReader(json_rpc_url, max_threads=16, notify=PrintProgressUpdate(), max_blocks_once=10_000)

    # Loop over the events as the multihreaded reader pool is feeding them to us.
    # Events will always arrive in the order they happened on chain.
    decoded_events = []
    start = datetime.datetime.utcnow()
    for event in reader(
        web3,
        start_block,
        end_block,
        filter=filter,
    ):
        # Decode the solidity event
        #
        # Indexed event parameters go to EVM topics, the second element is the first parameter
        # Non-indexed event parameters go to EVM arguments, first element is the first parameter
        arguments = decode_data(event["data"])
        topics = event["topics"]

        # event PrimitiveAdded(
        #     address indexed primitive,
        #     address aggregator,
        #     RateAsset rateAsset,
        #     uint256 unit
        # );
        primitive = convert_uint256_bytes_to_address(HexBytes(topics[1]))
        aggregator = convert_uint256_bytes_to_address(arguments[0])
        rate_asset = convert_int256_bytes_to_int(arguments[1])
        unit = convert_int256_bytes_to_int(arguments[2])

        # Primitive is a ERC-20 token, resolve its name and symbol while we are decoded the events
        token = fetch_erc20_details(web3, primitive)

        decoded = {
            "primitive": primitive,
            "aggregator": aggregator,
            "rate_asset": rate_asset,
            "unit": unit,
            "token": token,
        }

        decoded_events.append(decoded)

    reader.close()

    duration = datetime.datetime.utcnow() - start

    # Print out the results to the user at the end
    print(f"Found {len(decoded_events)}")
    for evt in decoded_events:
        print(f"   Token {evt['token'].symbol}: Chainlink aggregator is set to {evt['aggregator']}")

    api_counts = reader.get_total_api_call_counts()
    total = api_counts["total"]
    rate = total / duration.total_seconds()
    print(f"We did {total:,} JSON-RPC API requests, avg {rate:.2f} requests/second, as the run took {duration}")


if __name__ == "__main__":
    main()
