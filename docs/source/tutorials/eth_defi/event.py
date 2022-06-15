"""Solidity events scalable fetch and reading."""

from typing import Iterable, Optional, Type

from eth_abi.codec import ABICodec
from eth_typing import BlockNumber, HexAddress
from web3 import Web3
from web3._utils.events import get_event_data
from web3._utils.filters import construct_event_filter_params
from web3.contract import ContractEvent
from web3.datastructures import AttributeDict


def fetch_all_events(
    web3: Web3,
    event: Type[ContractEvent],
    address: Optional[HexAddress] = None,
    argument_filters: Optional[dict] = None,
    from_block: Optional[BlockNumber] = 1,
    to_block: Optional[BlockNumber] = None,
) -> Iterable[AttributeDict]:
    """Get events using eth_getLogs API.

    This is a stateless method, as oppose to JSON-RPC filter objects.
    It can be safely called against nodes which do not provide `eth_newFilter` API, like Infura.

    We are not doing any throttling or API error recovery:
    If you ask for too many events once this function and your Ethereum node are likely to blow up.

    Example how to get all ERC-20 transfers to a target address:

    .. code-block:: python

        IERC20 = get_contract(web3, "IERC20.json")
        Transfer = IERC20.events.Transfer
        all_transfers_to_user = list(fetch_all_events(web3, Transfer, argument_filters={"to": owner}))

    :param web3: Web3 instance
    :param event: Event class grabbed from a Contract proxy class, like `IERC20.events.Transfer`.
    :param address: The smart contract address of the event emitter. Set to none to capture events from all the smart contracts.
    :param argument_filters: Filters based on the event structure, e.g. `to` field `IERC20.events.Transfer`
    :param from_block: Limit block range. Set to `1` to get all the events, ever.
    :param to_block: Limit block range
    """

    # Currently no way to poke this using a public Web3.py API.
    # This will return raw underlying ABI JSON object for the event
    abi = event._get_event_abi()

    # Depending on the Solidity version used to compile
    # the contract that uses the ABI,
    # it might have Solidity ABI encoding v1 or v2.
    # We just assume the default that you set on Web3 object here.
    # More information here https://eth-abi.readthedocs.io/en/latest/index.html
    codec: ABICodec = web3.codec

    # Here we need to poke a bit into Web3 internals, as this
    # functionality is not exposed by default.
    # Construct JSON-RPC raw filter presentation based on human readable Python descriptions
    # Namely, convert event names to their keccak signatures
    # More information here:
    # https://github.com/ethereum/web3.py/blob/e176ce0793dafdd0573acc8d4b76425b6eb604ca/web3/_utils/filters.py#L71
    data_filter_set, event_filter_params = construct_event_filter_params(abi, codec, address=address, argument_filters=argument_filters, fromBlock=from_block, toBlock=to_block)

    # Call JSON-RPC API on your Ethereum node.
    # get_logs() returns raw AttributedDict entries
    logs = web3.eth.get_logs(event_filter_params)

    # Convert raw binary data to Python proxy objects as described by ABI
    for log in logs:
        # Convert raw JSON-RPC log result to human readable event by using ABI data
        # More information how processLog works here
        # https://github.com/ethereum/web3.py/blob/fbaf1ad11b0c7fac09ba34baff2c256cffe0a148/web3/_utils/events.py#L200
        evt = get_event_data(codec, abi, log)
        yield evt
