"""Token holding and portfolio for addresses."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict

from eth_typing import ChecksumAddress, BlockNumber, HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_hentai.abi import get_contract
from eth_hentai.event import fetch_all_events


@dataclass
class DecimalisedHolding:
    """A helper class to represent token holdings.

    Exposes the underlying decimals the ERC-20 wants to express.
    """
    value: Decimal
    decimals: int
    contract: Contract


def fetch_erc20_balances(web3: Web3, owner: HexAddress, last_block_num: Optional[BlockNumber] = None) -> Dict[HexAddress, int]:
    """Get all current holdings of an account.

    We attempt to build a list of token holdings by analysing incoming ERC-20 Transfer events to a wallet.

    The blockchain native currency like `ETH` or `MATIC` is not included in the analysis, because native
    currency transfers do not generate events.

    We are not doing any throttling: If you ask for too many events once this function and your
    Ethereum node are likely to blow up.

    Example:

    .. code-block:: python

        # Load up the user with some tokens
        usdc.functions.transfer(user_1, 500).transact({"from": deployer})
        aave.functions.transfer(user_1, 200).transact({"from": deployer})
        balances = fetch_erc20_balances(web3, user_1)
        assert balances[usdc.address] == 500
        assert balances[aave.address] == 200


    :param web3: Web3 instance
    :param owner: The address we are analysis
    :param last_block_num: Set to the last block, inclusive, if you want to have an analysis of in a point of history.
    :return: Map of (token address, amount)
    """

    IERC20 = get_contract(web3, "IERC20.json")
    Transfer = IERC20.events.Transfer

    balances = Counter()

    # Iterate over all ERC-20 transfer events to the address
    for transfer in fetch_all_events(web3, Transfer, argument_filters={"to": owner}, to_block=last_block_num):
        # transfer is AttributeDict({'args': AttributeDict({'from': '0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf', 'to': '0x2B5AD5c4795c026514f8317c7a215E218DcCD6cF', 'value': 200}), 'event': 'Transfer', 'logIndex': 0, 'transactionIndex': 0, 'transactionHash': HexBytes('0xd3fef67dbded34f1f7b2ec5217e5dfd5e4d9ad0fda66a8da925722f1e62518c8'), 'address': '0x2946259E0334f33A064106302415aD3391BeD384', 'blockHash': HexBytes('0x55618d13d644f35a8639671561c2f9a93958eae055c754531b124735f92b429b'), 'blockNumber': 4})
        erc20_smart_contract = transfer["address"]
        value = transfer["args"]["value"]
        balances[erc20_smart_contract] += value

    return balances


def convert_to_decimal(web3, raw_balances: Dict[HexAddress, int]) -> Dict[HexAddress, DecimalisedHolding]:
    """Convert mapping of ERC-20 holdings to decimals.

    Issues a JSON-RPC call to fetch token data for each ERC-20 in the input dictionary.
    """

    # decimals() is not part of core ERC-20 interface,
    # use OpenZeppein contract here
    ERC20 = get_contract(web3, "ERC20MockDecimals.json")

    res = {}

    for address, raw_balance in raw_balances.items():
        contract = ERC20(address)
        decimals = contract.functions.decimals().call()
        res[address] = DecimalisedHolding(Decimal(raw_balance) / Decimal(10**decimals), decimals, contract)

    return res


def fetch_erc20_balances_decimal(web3: Web3, owner: HexAddress, last_block_num: Optional[BlockNumber] = None) -> Dict[HexAddress, DecimalisedHolding]:
    """Get all current holdings of an account.

    Convert holdings to the natural decimal format.

    Example:

    .. code-block:: python

        # Load up the user with some tokens
        usdc.functions.transfer(user_1, 500).transact({"from": deployer})
        aave.functions.transfer(user_1, 200).transact({"from": deployer})
        balances = fetch_erc20_balances(web3, user_1)
        assert balances[usdc.address] == 500
        assert balances[aave.address] == 200


    :param web3: Web3 instance
    :param owner: The address we are analysis
    :param last_block_num: Set to the last block, inclusive, if you want to have an analysis of in a point of history.
    :return: Map of (token address, amount)
    """

    raw_balances = fetch_erc20_balances(web3, owner, last_block_num)
    return convert_to_decimal(web3, raw_balances)
